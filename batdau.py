import random # Đây là mấy dòng linh tinh
import logging
import subprocess
import sys
import os
import re
import time
import concurrent.futures
import discord
from discord.ext import commands, tasks
import docker
import asyncio
from discord import app_commands

TOKEN = '' # DÁN TOKEN VÀO ĐÂY
RAM_LIMIT = '8g'
SERVER_LIMIT = 1000
database_file = 'database.txt'

intents = discord.Intents.default()
intents.messages = False
intents.message_content = False

bot = commands.Bot(command_prefix='/', intents=intents)
client = docker.from_env()

# module sinh cổng và chuyển tiếp cổng < tui quên mất cái này lúc đầu
def generate_random_port(): 
    return random.randint(1025, 65535)

def add_to_database(user, container_name, ssh_command):
    with open(database_file, 'a') as f:
        f.write(f"{user}|{container_name}|{ssh_command}\n")

def remove_from_database(ssh_command):
    if not os.path.exists(database_file):
        return
    with open(database_file, 'r') as f:
        lines = f.readlines()
    with open(database_file, 'w') as f:
        for line in lines:
            if ssh_command not in line:
                f.write(line)

async def capture_ssh_session_line(process):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if "ssh session:" in output:
            return output.split("ssh session:")[1].strip()
    return None

def get_ssh_command_from_database(container_id):
    if not os.path.exists(database_file):
        return None
    with open(database_file, 'r') as f:
        for line in f:
            if container_id in line:
                return line.split('|')[2]
    return None

def get_user_servers(user):
    if not os.path.exists(database_file):
        return []
    servers = []
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(user):
                servers.append(line.strip())
    return servers

def count_user_servers(user):
    return len(get_user_servers(user))

def get_container_id_from_database(user):
    servers = get_user_servers(user)
    if servers:
        return servers[0].split('|')[1]
    return None

@bot.event
async def on_ready():
    change_status.start()
    print(f'Bot đã sẵn sàng. Đăng nhập với tên {bot.user}')
    await bot.tree.sync()

@tasks.loop(seconds=5)
async def change_status():
    try:
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                lines = f.readlines()
                instance_count = len(lines)
        else:
            instance_count = 0

        status = f"với {instance_count} máy chủ đám mây"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Không thể cập nhật trạng thái: {e}")

async def regen_ssh_command(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="Không tìm thấy máy chủ nào đang hoạt động cho tài khoản của bạn.", color=0xff0000))
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Lỗi khi chạy tmate trong container Docker: {e}", color=0xff0000))
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        await interaction.user.send(embed=discord.Embed(description=f"### Lệnh SSH mới: ```{ssh_session_line}```", color=0x00ff00))
        await interaction.response.send_message(embed=discord.Embed(description="Đã tạo lại phiên SSH mới. Hãy kiểm tra tin nhắn riêng để xem chi tiết.", color=0x00ff00))
    else:
        await interaction.response.send_message(embed=discord.Embed(description="Không thể tạo phiên SSH mới.", color=0xff0000))

async def start_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="Không tìm thấy máy chủ nào cho tài khoản của bạn.", color=0xff0000))
        return

    try:
        subprocess.run(["docker", "start", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(description=f"### Máy chủ đã khởi động\nLệnh SSH: ```{ssh_session_line}```", color=0x00ff00))
            await interaction.response.send_message(embed=discord.Embed(description="Máy chủ đã được khởi động thành công. Kiểm tra tin nhắn riêng để xem chi tiết.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Máy chủ đã khởi động, nhưng không lấy được lệnh SSH.", color=0xff0000))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Lỗi khi khởi động máy chủ: {e}", color=0xff0000))

async def stop_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="Không tìm thấy máy chủ nào cho tài khoản của bạn.", color=0xff0000))
        return

    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        await interaction.response.send_message(embed=discord.Embed(description="Máy chủ đã được dừng thành công.", color=0x00ff00))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Lỗi khi dừng máy chủ: {e}", color=0xff0000))

async def restart_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="Không tìm thấy máy chủ nào cho tài khoản của bạn.", color=0xff0000))
        return

    try:
        subprocess.run(["docker", "restart", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(description=f"### Máy chủ đã khởi động lại\nLệnh SSH: ```{ssh_session_line}```\nHệ điều hành: Ubuntu 22.04", color=0x00ff00))
            await interaction.response.send_message(embed=discord.Embed(description="Máy chủ đã khởi động lại thành công. Kiểm tra tin nhắn riêng để xem chi tiết.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Máy chủ đã khởi động lại, nhưng không lấy được lệnh SSH.", color=0xff0000))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Lỗi khi khởi động lại máy chủ: {e}", color=0xff0000))

def get_container_id_from_database(user, container_name):
    if not os.path.exists(database_file):
        return None
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(user) and container_name in line:
                return line.split('|')[1]
    return None

async def execute_command(command):
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return stdout.decode(), stderr.decode()

PUBLIC_IP = '138.68.79.95'

async def capture_output(process, keyword):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if keyword in output:
            return output
    return None

@bot.tree.command(name="port-add", description="Thêm quy tắc chuyển tiếp cổng")
@app_commands.describe(container_name="Tên của container", container_port="Cổng bên trong container")
async def port_add(interaction: discord.Interaction, container_name: str, container_port: int):
    await interaction.response.send_message(embed=discord.Embed(description="Đang thiết lập chuyển tiếp cổng. Việc này có thể mất một chút thời gian...", color=0x00ff00))

    public_port = generate_random_port()

    # Thiết lập chuyển tiếp cổng bên trong container
    command = f"ssh -o StrictHostKeyChecking=no -R {public_port}:localhost:{container_port} serveo.net -N -f"

    try:
        # Chạy lệnh trong nền bằng Docker exec
        await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "bash", "-c", command,
            stdout=asyncio.subprocess.DEVNULL,  # Không cần lấy đầu ra
            stderr=asyncio.subprocess.DEVNULL  # Không cần lấy lỗi
        )

        # Phản hồi ngay lập tức với cổng và IP công cộng
        await interaction.followup.send(embed=discord.Embed(description=f"Đã thêm cổng thành công. Dịch vụ của bạn đang chạy tại {PUBLIC_IP}:{public_port}.", color=0x00ff00))

    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(description=f"Đã xảy ra lỗi không mong muốn: {e}", color=0xff0000))

@bot.tree.command(name="port-http", description="Chuyển tiếp lưu lượng HTTP đến container của bạn")
@app_commands.describe(container_name="Tên container của bạn", container_port="Cổng bên trong container cần chuyển tiếp")
async def port_forward_website(interaction: discord.Interaction, container_name: str, container_port: int):
    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "ssh", "-o StrictHostKeyChecking=no", "-R", f"80:localhost:{container_port}", "serveo.net",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        url_line = await capture_output(exec_cmd, "Forwarding HTTP traffic from")
        if url_line:
            url = url_line.split(" ")[-1]
            await interaction.response.send_message(embed=discord.Embed(description=f"Đã chuyển tiếp website thành công. Website của bạn có thể truy cập tại {url}.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Không thể lấy được URL chuyển tiếp.", color=0xff0000))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Lỗi khi chuyển tiếp website: {e}", color=0xff0000))

async def create_server_task(interaction):
    await interaction.response.send_message(embed=discord.Embed(description="Đang tạo máy chủ, việc này mất vài giây.", color=0x00ff00))
    user = str(interaction.user)
    if count_user_servers(user) >= SERVER_LIMIT:
        await interaction.followup.send(embed=discord.Embed(description="```Lỗi: Đã đạt giới hạn máy chủ```", color=0xff0000))
        return

    image = "ubuntu-22.04-with-tmate"
    
    try:
        container_id = subprocess.check_output([
            "docker", "run", "-itd", "--privileged", "--cap-add=ALL", image
        ]).strip().decode('utf-8')
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(description=f"Lỗi khi tạo container Docker: {e}", color=0xff0000))
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(description=f"Lỗi khi chạy tmate trong container Docker: {e}", color=0xff0000))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        await interaction.user.send(embed=discord.Embed(description=f"### Đã tạo máy chủ thành công\nLệnh SSH: ```{ssh_session_line}```\nHệ điều hành: Ubuntu 22.04", color=0x00ff00))
        add_to_database(user, container_id, ssh_session_line)
        await interaction.followup.send(embed=discord.Embed(description="Máy chủ đã được tạo thành công. Kiểm tra tin nhắn riêng để xem chi tiết.", color=0x00ff00))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Đã xảy ra lỗi hoặc máy chủ mất quá nhiều thời gian để khởi động. Nếu lỗi tiếp tục, vui lòng liên hệ hỗ trợ.", color=0xff0000))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])

async def create_server_task_debian(interaction):
    await interaction.response.send_message(embed=discord.Embed(description="Đang tạo máy chủ, việc này mất vài giây.", color=0x00ff00))
    user = str(interaction.user)
    if count_user_servers(user) >= SERVER_LIMIT:
        await interaction.followup.send(embed=discord.Embed(description="```Lỗi: Đã đạt giới hạn máy chủ```", color=0xff0000))
        return

    image = "debian-with-tmate"
    
    try:
        container_id = subprocess.check_output([
            "docker", "run", "-itd", "--privileged", "--cap-add=ALL", image
        ]).strip().decode('utf-8')
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(description=f"Lỗi khi tạo container Docker: {e}", color=0xff0000))
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(description=f"Lỗi khi chạy tmate trong container Docker: {e}", color=0xff0000))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        await interaction.user.send(embed=discord.Embed(description=f"### Đã tạo máy chủ thành công\nLệnh SSH: ```{ssh_session_line}```\nHệ điều hành: Debian", color=0x00ff00))
        add_to_database(user, container_id, ssh_session_line)
        await interaction.followup.send(embed=discord.Embed(description="Máy chủ đã được tạo thành công. Kiểm tra tin nhắn riêng để xem chi tiết.", color=0x00ff00))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Đã xảy ra lỗi hoặc máy chủ mất quá nhiều thời gian để khởi động. Nếu lỗi tiếp tục, vui lòng liên hệ hỗ trợ.", color=0xff0000))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])

@bot.tree.command(name="deploy-ubuntu", description="Tạo một máy chủ mới với Ubuntu 22.04")
async def deploy_ubuntu(interaction: discord.Interaction):
    await create_server_task(interaction)

@bot.tree.command(name="deploy-debian", description="Tạo một máy chủ mới với Debian 12")
async def deploy_ubuntu(interaction: discord.Interaction):
    await create_server_task_debian(interaction)

@bot.tree.command(name="regen-ssh", description="Tạo lại phiên SSH cho máy chủ của bạn")
@app_commands.describe(container_name="Tên/lệnh SSH của máy chủ")
async def regen_ssh(interaction: discord.Interaction, container_name: str):
    await regen_ssh_command(interaction, container_name)

@bot.tree.command(name="start", description="Khởi động máy chủ của bạn")
@app_commands.describe(container_name="Tên/lệnh SSH của máy chủ")
async def start(interaction: discord.Interaction, container_name: str):
    await start_server(interaction, container_name)

@bot.tree.command(name="stop", description="Dừng máy chủ của bạn")
@app_commands.describe(container_name="Tên/lệnh SSH của máy chủ")
async def stop(interaction: discord.Interaction, container_name: str):
    await stop_server(interaction, container_name)

@bot.tree.command(name="restart", description="Khởi động lại máy chủ của bạn")
@app_commands.describe(container_name="Tên/lệnh SSH của máy chủ")
async def restart(interaction: discord.Interaction, container_name: str):
    await restart_server(interaction, container_name)

@bot.tree.command(name="ping", description="Kiểm tra độ trễ của bot.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Độ trễ: {latency}ms",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="list", description="Liệt kê tất cả các máy chủ của bạn")
async def list_servers(interaction: discord.Interaction):
    user = str(interaction.user)
    servers = get_user_servers(user)
    if servers:
        embed = discord.Embed(title="Các máy chủ của bạn", color=0x00ff00)
        for server in servers:
            _, container_name, _ = server.split('|')
            embed.add_field(name=container_name, value="Mô tả: Một máy chủ với 32GB RAM và 8 nhân.", inline=False)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(embed=discord.Embed(description="Bạn không có máy chủ nào.", color=0xff0000))

@bot.tree.command(name="remove", description="Xóa một máy chủ")
@app_commands.describe(container_name="Tên/lệnh SSH của máy chủ")
async def remove_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="Không tìm thấy máy chủ nào với tên này cho tài khoản của bạn.", color=0xff0000))
        return

    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        subprocess.run(["docker", "rm", container_id], check=True)
        
        remove_from_database(container_id)
        
        await interaction.response.send_message(embed=discord.Embed(description=f"Máy chủ '{container_name}' đã được xóa thành công.", color=0x00ff00))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Lỗi khi xóa máy chủ: {e}", color=0xff0000))

@bot.tree.command(name="help", description="Hiển thị trợ giúp")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Trợ giúp", color=0x00ff00)
    embed.add_field(name="/deploy-ubuntu", value="Tạo một máy chủ mới với Ubuntu 22.04.", inline=False)
    embed.add_field(name="/deploy-debian", value="Tạo một máy chủ mới với Debian 12.", inline=False)
    embed.add_field(name="/remove <lệnh SSH/Tên>", value="Xóa một máy chủ", inline=False)
    embed.add_field(name="/start <lệnh SSH/Tên>", value="Khởi động một máy chủ.", inline=False)
    embed.add_field(name="/stop <lệnh SSH/Tên>", value="Dừng một máy chủ.", inline=False)
    embed.add_field(name="/regen-ssh <lệnh SSH/Tên>", value="Tạo lại thông tin SSH", inline=False)
    embed.add_field(name="/restart <lệnh SSH/Tên>", value="Khởi động lại một máy chủ.", inline=False)
    embed.add_field(name="/list", value="Liệt kê tất cả máy chủ của bạn", inline=False)
    embed.add_field(name="/ping", value="Kiểm tra độ trễ của bot.", inline=False)
    embed.add_field(name="/port-http", value="Chuyển tiếp website HTTP.", inline=False)
    embed.add_field(name="/port-add", value="Chuyển tiếp một cổng.", inline=False)
    await interaction.response.send_message(embed=embed)

bot.run(TOKEN)

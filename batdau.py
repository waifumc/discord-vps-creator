import random
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

# === CẤU HÌNH ===
TOKEN = 'MTQwNzk3NzQwMTc3ODE3NjA1Mg.Gtogyt.L232K1xbf2U6GSu3Mai07FQX8whA4srQA9cdgg'  # NHẬP TOKEN VÀO ĐÂY
SERVER_LIMIT = 100
database_file = 'database.txt'

# === BOT SETUP ===
intents = discord.Intents.default()
intents.messages = False
intents.message_content = False

bot = commands.Bot(command_prefix='/', intents=intents)
client = docker.from_env()

# === HÀM HỖ TRỢ ===
def generate_random_port():
    return random.randint(1025, 65535)

def sanitize_username(username):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', str(username))[:24]

def generate_password():
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$'
    return ''.join(random.choices(chars, k=12))

# === DATABASE ===
def add_to_database(user, container_name, ssh_command, password):
    with open(database_file, 'a') as f:
        f.write(f"{user}|{container_name}|{ssh_command}|{password}\n")

def remove_from_database(container_name):
    if not os.path.exists(database_file):
        return
    with open(database_file, 'r') as f:
        lines = f.readlines()
    with open(database_file, 'w') as f:
        for line in lines:
            if container_name not in line:
                f.write(line)

def get_container_info(user, identifier):
    """Trả về (container_name, ssh_command, password) nếu tìm thấy"""
    if not os.path.exists(database_file):
        return None
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(user):
                parts = line.strip().split('|')
                if len(parts) >= 4:
                    container_name = parts[1]
                    ssh_command = parts[2]
                    password = parts[3]
                    if identifier == container_name or identifier in ssh_command:
                        return container_name, ssh_command, password
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

# === BOT EVENTS ===
@bot.event
async def on_ready():
    change_status.start()
    print(f'Bot đã sẵn sàng. Đăng nhập với tên {bot.user}')
    await bot.tree.sync()

@tasks.loop(seconds=5)
async def change_status():
    try:
        count = 0
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                count = len(f.readlines())
        status = f"với {count} Máy Chủ Đám Mây"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Không thể cập nhật trạng thái: {e}")

# === HÀM CAPTURE SSH ===
async def capture_ssh_session_line(process):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if "ssh session:" in output:
            return output.split("ssh session:")[1].strip()
    return None

# === CÁC HÀM QUẢN LÝ MÁY CHỦ ===
async def create_server_task(interaction, image_name, os_name):
    await interaction.response.send_message(embed=discord.Embed(description="🛠️ Đang tạo máy chủ...", color=0x00ff00))
    user = str(interaction.user)
    if count_user_servers(user) >= SERVER_LIMIT:
        await interaction.followup.send(embed=discord.Embed(description="❌ Đã đạt giới hạn 12 máy chủ.", color=0xff0000))
        return

    # Tạo tên container duy nhất
    base_name = sanitize_username(user)
    container_name = f"cloud_{base_name}_{random.randint(1000, 9999)}"
    password = generate_password()

    try:
        # Chạy container với systemd
        subprocess.run([
            "docker", "run", "-d",
            "--privileged",
            "--cap-add=ALL",
            "--tmpfs", "/run",
            "--tmpfs", "/run/lock",
            "--tmpfs", "/tmp",
            "-v", "/sys/fs/cgroup:/sys/fs/cgroup:ro",
            "--hostname", "idlernetwork",
            "--name", container_name,
            image_name
        ], check=True, capture_output=True)

        # Đổi mật khẩu root
        subprocess.run([
            "docker", "exec", container_name, "bash", "-c", f"echo 'root:{password}' | chpasswd"
        ], check=True)

        # Chạy tmate để lấy SSH
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(exec_cmd)

        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(
                description=f"### ✅ Máy Chủ Đã Tạo\n"
                            f"**SSH:** ```{ssh_session_line}```\n"
                            f"**Hệ điều hành:** {os_name}\n"
                            f"**Hostname:** `idlernetwork`\n"
                            f"**Mật khẩu root:** `{password}`",
                color=0x00ff00
            ))
            add_to_database(user, container_name, ssh_session_line, password)
            await interaction.followup.send(embed=discord.Embed(description="✅ Máy chủ đã tạo. Kiểm tra tin nhắn riêng!", color=0x00ff00))
        else:
            await interaction.followup.send(embed=discord.Embed(description="❌ Không thể lấy SSH. Xóa container.", color=0xff0000))
            subprocess.run(["docker", "rm", "-f", container_name])
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        await interaction.followup.send(embed=discord.Embed(description=f"❌ Lỗi Docker: {error_msg}", color=0xff0000))
        subprocess.run(["docker", "rm", "-f", container_name])

@bot.tree.command(name="deploy-ubuntu", description="Tạo máy chủ Ubuntu 22.04")
async def deploy_ubuntu(interaction: discord.Interaction):
    await create_server_task(interaction, "ubuntu-22.04-with-tmate", "Ubuntu 22.04")

@bot.tree.command(name="deploy-debian", description="Tạo máy chủ Debian 12")
async def deploy_debian(interaction: discord.Interaction):  # ← Đã sửa tên
    await create_server_task(interaction, "debian-with-tmate", "Debian 12")

# === Regen SSH ===
async def regen_ssh_command(interaction: discord.Interaction, identifier: str):
    user = str(interaction.user)
    info = get_container_info(user, identifier)
    if not info:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Không tìm thấy máy chủ của bạn.", color=0xff0000))
        return
    container_name, _, password = info

    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(description=f"### 🔁 Lệnh SSH Mới\n```{ssh_session_line}```\n**Mật khẩu root:** `{password}`", color=0x00ff00))
            await interaction.response.send_message(embed=discord.Embed(description="✅ Đã tạo lại SSH. Kiểm tra tin nhắn riêng.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="❌ Không thể tạo lại SSH.", color=0xff0000))
    except Exception as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"❌ Lỗi: {e}", color=0xff0000))

@bot.tree.command(name="regen-ssh", description="Tạo lại phiên SSH")
@app_commands.describe(identifier="Tên container hoặc lệnh SSH")
async def regen_ssh(interaction: discord.Interaction, identifier: str):
    await regen_ssh_command(interaction, identifier)

# === Start ===
async def start_server(interaction: discord.Interaction, identifier: str):
    user = str(interaction.user)
    info = get_container_info(user, identifier)
    if not info:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Không tìm thấy máy chủ.", color=0xff0000))
        return
    container_name, _, password = info

    try:
        subprocess.run(["docker", "start", container_name], check=True)
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(description=f"### ▶️ Máy Chủ Đã Bắt Đầu\n**SSH:** ```{ssh_session_line}```\n**Mật khẩu root:** `{password}`", color=0x00ff00))
            await interaction.response.send_message(embed=discord.Embed(description="✅ Khởi động thành công. Kiểm tra tin nhắn riêng.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="⚠️ Không lấy được SSH.", color=0xff8800))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"❌ Lỗi: {e}", color=0xff0000))

@bot.tree.command(name="start", description="Khởi động máy chủ")
@app_commands.describe(identifier="Tên hoặc lệnh SSH")
async def start(interaction: discord.Interaction, identifier: str):
    await start_server(interaction, identifier)

# === Stop ===
@bot.tree.command(name="stop", description="Dừng máy chủ")
@app_commands.describe(identifier="Tên hoặc lệnh SSH")
async def stop(interaction: discord.Interaction, identifier: str):
    user = str(interaction.user)
    info = get_container_info(user, identifier)
    if not info:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Không tìm thấy.", color=0xff0000))
        return
    container_name, _, _ = info
    try:
        subprocess.run(["docker", "stop", container_name], check=True)
        await interaction.response.send_message(embed=discord.Embed(description="⏹️ Đã dừng máy chủ.", color=0x00ff00))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"❌ Lỗi: {e}", color=0xff0000))

# === Restart ===
@bot.tree.command(name="restart", description="Khởi động lại")
@app_commands.describe(identifier="Tên hoặc lệnh SSH")
async def restart(interaction: discord.Interaction, identifier: str):
    user = str(interaction.user)
    info = get_container_info(user, identifier)
    if not info:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Không tìm thấy.", color=0xff0000))
        return
    container_name, _, password = info
    try:
        subprocess.run(["docker", "restart", container_name], check=True)
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(description=f"### 🔁 Khởi động lại\n**SSH:** ```{ssh_session_line}```\n**Mật khẩu root:** `{password}`", color=0x00ff00))
            await interaction.response.send_message(embed=discord.Embed(description="✅ Đã khởi động lại. Kiểm tra tin nhắn riêng.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="⚠️ Không lấy được SSH.", color=0xff8800))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"❌ Lỗi: {e}", color=0xff0000))

# === List ===
@bot.tree.command(name="list", description="Liệt kê máy chủ của bạn")
async def list_servers(interaction: discord.Interaction):
    user = str(interaction.user)
    servers = get_user_servers(user)
    if servers:
        embed = discord.Embed(title="🖥️ Máy Chủ Của Bạn", color=0x00ff00)
        for s in servers:
            _, name, ssh, _ = s.split('|', 3)
            embed.add_field(name=name, value=f"SSH: `{ssh[:50]}...`", inline=False)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Bạn chưa có máy chủ nào.", color=0xff0000))

# === Remove ===
@bot.tree.command(name="remove", description="Xóa máy chủ")
@app_commands.describe(identifier="Tên hoặc lệnh SSH")
async def remove_server(interaction: discord.Interaction, identifier: str):
    user = str(interaction.user)
    info = get_container_info(user, identifier)
    if not info:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Không tìm thấy.", color=0xff0000))
        return
    container_name, _, _ = info
    try:
        subprocess.run(["docker", "stop", container_name], check=True)
        subprocess.run(["docker", "rm", container_name], check=True)
        remove_from_database(container_name)
        await interaction.response.send_message(embed=discord.Embed(description=f"🗑️ Máy chủ `{container_name}` đã bị xóa.", color=0x00ff00))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"❌ Lỗi: {e}", color=0xff0000))

# === Ping ===
@bot.tree.command(name="ping", description="Kiểm tra độ trễ")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(embed=discord.Embed(title="🏓 Pong!", description=f"Độ trễ: {latency}ms", color=0x00ff00))

# === Help ===
@bot.tree.command(name="help", description="Hướng dẫn sử dụng")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="📘 Trợ giúp", color=0x00ff00)
    embed.add_field(name="/deploy-ubuntu", value="Tạo Ubuntu 22.04", inline=False)
    embed.add_field(name="/deploy-debian", value="Tạo Debian 12", inline=False)
    embed.add_field(name="/start <tên>", value="Khởi động", inline=False)
    embed.add_field(name="/stop <tên>", value="Dừng", inline=False)
    embed.add_field(name="/restart <tên>", value="Khởi động lại", inline=False)
    embed.add_field(name="/regen-ssh <tên>", value="Tạo lại SSH", inline=False)
    embed.add_field(name="/remove <tên>", value="Xóa máy chủ", inline=False)
    embed.add_field(name="/list", value="Xem danh sách", inline=False)
    embed.add_field(name="/ping", value="Kiểm tra độ trễ", inline=False)
    embed.add_field(name="/port-http", value="Chuyển tiếp HTTP", inline=False)
    embed.add_field(name="/port-add", value="Chuyển tiếp cổng", inline=False)
    await interaction.response.send_message(embed=embed)

# === Port Forwarding ===
PUBLIC_IP = '138.68.79.95'

@bot.tree.command(name="port-add", description="Chuyển tiếp cổng")
@app_commands.describe(container_name="Tên container", container_port="Cổng trong container")
async def port_add(interaction: discord.Interaction, container_name: str, container_port: int):
    await interaction.response.send_message(embed=discord.Embed(description="🔧 Đang thiết lập...", color=0x00ff00))
    public_port = generate_random_port()
    command = f"ssh -o StrictHostKeyChecking=no -R {public_port}:localhost:{container_port} serveo.net -N -f"
    try:
        await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "bash", "-c", command,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await interaction.followup.send(embed=discord.Embed(description=f"✅ Thành công! Truy cập tại `{PUBLIC_IP}:{public_port}`", color=0x00ff00))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(description=f"❌ Lỗi: {e}", color=0xff0000))

# === Chạy bot ===
bot.run(TOKEN)

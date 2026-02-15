import discord
from discord import app_commands
import mercadopago
from flask import Flask, request
import threading
import asyncio
import os
import sys

print("🔧 Iniciando bot...")

# ===============================
# CONFIG (VEM DO RENDER)
# ===============================
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
MP_ACCESS_TOKEN = os.environ["MP_ACCESS_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

ARQUIVO_PRODUTO = "produto.txt"

if not os.path.exists(ARQUIVO_PRODUTO):
    print("❌ produto.txt não encontrado")
    sys.exit()

# ===============================
# MERCADO PAGO
# ===============================
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def criar_pagamento(user_id):
    pref = sdk.preference().create({
        "items": [{
            "title": "Pack Premium",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": 24.99
        }],
        "notification_url": WEBHOOK_URL,
        "external_reference": f"cs_{user_id}"
    })

    if pref["status"] != 201:
        print("❌ MP erro:", pref)
        return None

    return pref["response"]["init_point"]


def criar_pagamento_rockstar(user_id):
    pref = sdk.preference().create({
        "items": [{
            "title": "Conta Rockstar",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": 4.99
        }],
        "notification_url": WEBHOOK_URL,
        "external_reference": f"rockstar_{user_id}"
    })

    if pref["status"] != 201:
        print("❌ MP erro:", pref)
        return None

    return pref["response"]["init_point"]

# ===============================
# DISCORD
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands sincronizados")

    async def on_ready(self):
        print(f"🟢 Logado como {self.user}")

    async def on_member_join(self, member):
        cargo_membro = member.guild.get_role(1472666559049633952)
        if cargo_membro:
            await member.add_roles(cargo_membro)
            print("👤 Novo membro recebeu cargo MEMBRO")

bot = Bot()

# ===============================
# COMANDO /COMPRAR CS
# ===============================
@bot.tree.command(name="comprar", description="Comprar Pack Premium")
async def comprar(interaction: discord.Interaction):

    link = criar_pagamento(interaction.user.id)

    if not link:
        await interaction.response.send_message("❌ Erro pagamento", ephemeral=True)
        return

    embed = discord.Embed(
        title="🔥 Cheat Counter Strike",
        description="✅ Acesso completo\n✅ Arquivos exclusivos\n✅ Suporte VIP\n✅ Entrega Automática",
        color=0x00ff88
    )

    embed.add_field(name="💰 Preço", value="R$ 24,99", inline=False)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="🛒 Comprar", url=link))

    await interaction.response.send_message(embed=embed, view=view)

# ===============================
# COMANDO /COMPRAR ROCKSTAR
# ===============================
@bot.tree.command(name="comprar_rockstar", description="Comprar Conta Rockstar")
async def comprar_rockstar(interaction: discord.Interaction):

    link = criar_pagamento_rockstar(interaction.user.id)

    embed = discord.Embed(
        title="🎮 Conta Rockstar",
        description="✅ Conta pronta\n✅ Entrega manual\n✅ Garantia",
        color=0x3498db
    )

    embed.add_field(name="💰 Preço", value="R$ 4,99", inline=False)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="🛒 Comprar", url=link))

    await interaction.response.send_message(embed=embed, view=view)

# ===============================
# WEBHOOK
# ===============================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 webhook:", data)

    try:
        payment_id = data["data"]["id"]
        payment = sdk.payment().get(payment_id)["response"]

        if payment["status"] == "approved":

            ref = payment["external_reference"]

            if ref.startswith("cs_"):
                uid = int(ref.replace("cs_", ""))
                asyncio.run_coroutine_threadsafe(enviar_produto(uid), bot.loop)

            elif ref.startswith("rockstar_"):
                uid = int(ref.replace("rockstar_", ""))
                asyncio.run_coroutine_threadsafe(enviar_produto_manual(uid), bot.loop)

    except Exception as e:
        print("❌ webhook erro:", e)

    return "OK", 200

# ===============================
# ENTREGA AUTOMÁTICA
# ===============================
async def enviar_produto(user_id):

    user = await bot.fetch_user(user_id)

    await user.send(
        "✅ Pagamento confirmado! Aqui está seu produto:",
        file=discord.File(ARQUIVO_PRODUTO)
    )

    guild = bot.get_guild(1472114509068898367)
    member = guild.get_member(user_id)

    if member:
        await member.remove_roles(guild.get_role(1472666559049633952))
        await member.add_roles(guild.get_role(1472666841515032676))

    print("📦 Produto CS entregue")

# ===============================
# ENTREGA MANUAL
# ===============================
async def enviar_produto_manual(user_id):

    user = await bot.fetch_user(user_id)

    await user.send(
        "✅ Pagamento aprovado!\n"
        "📦 Sua Conta Rockstar será entregue em breve por um administrador."
    )

    print("📨 Aviso manual enviado")

# ===============================
# FLASK
# ===============================
def iniciar_flask():
    print("🌐 Flask online")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def start_all():
    threading.Thread(target=iniciar_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_all()

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

# URL do webhook será automática no Render:
# https://SEUAPP.onrender.com/webhook
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
        "external_reference": str(user_id)
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
intents.dm_messages = True

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
        try:
            guild = member.guild
            cargo_membro = guild.get_role(1472666559049633952)

            if cargo_membro:
                await member.add_roles(cargo_membro)
                print("👤 Novo membro recebeu cargo MEMBRO")

        except Exception as e:
            print("❌ Erro ao dar cargo membro:", e)

bot = Bot()

# ===============================
# COMANDO /COMPRAR
# ===============================
@bot.tree.command(name="comprar", description="Comprar Pack Premium")
async def comprar(interaction: discord.Interaction):

    link = criar_pagamento(interaction.user.id)

    if not link:
        await interaction.response.send_message(
            "❌ Erro ao gerar pagamento",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🔥 Cheat Counter Strike ",
        description="✅ Acesso completo\n✅ Arquivos exclusivos\n✅ Suporte VIP\n✅ Entrega Automatica",
        color=0x00ff88
    )

    embed.add_field(
        name="💰 Preço",
        value="R$ 24,99",
        inline=False
    )

    embed.set_image(
        url="https://media.discordapp.net/attachments/1472115534538473603/1472352321500483645/VELAR_1.png"
    )

    embed.set_footer(
        text="Clique no botão abaixo para comprar"
    )

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="🛒 Comprar Agora",
        url=link
    ))

    await interaction.response.send_message(
        embed=embed,
        view=view
    )

# ===============================
# FLASK WEBHOOK
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
            user_id = int(payment["external_reference"])

            asyncio.run_coroutine_threadsafe(
                enviar_produto(user_id),
                bot.loop
            )

    except Exception as e:
        print("❌ webhook erro:", e)

    return "OK", 200


async def enviar_produto(user_id):
    try:
        user = await bot.fetch_user(user_id)

        await user.send(
            "✅ Pagamento confirmado! Aqui está seu produto:",
            file=discord.File(ARQUIVO_PRODUTO)
        )
        # ===== DAR CARGO CLIENTE =====
        guild = bot.get_guild(1472114509068898367)
        member = guild.get_member(user_id)

        if member:
            cargo_cliente = guild.get_role(1472666841515032676)
            cargo_membro = guild.get_role(1472666559049633952)

            if cargo_membro:
                await member.remove_roles(cargo_membro)

            if cargo_cliente:
                await member.add_roles(cargo_cliente)

            print("🏷️ Cargo CLIENTE aplicado")

        print("📦 Produto enviado")

    except Exception as e:
        print("❌ Erro enviando produto:", e)


def iniciar_flask():
    print("🌐 Flask online")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# ===============================
# START (usado local e render)
# ===============================
def start_all():
    threading.Thread(target=iniciar_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_all()

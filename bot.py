import discord
from discord import app_commands
import mercadopago
from flask import Flask, request
import threading
import asyncio
import os
import sys
import time
import base64
import json
from datetime import datetime
from io import BytesIO
import pyotp

print("🔧 Iniciando bot...")

# ===============================
# CONFIG
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
WEBHOOK_URL = os.environ.get(
    "WEBHOOK_URL",
    "https://bot-discord-loja-eg7u.onrender.com/webhook"
)

ARQUIVO_PRODUTO = "produto.txt"
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

if os.path.exists(ARQUIVO_PRODUTO):
    print("📄 produto.txt encontrado")
else:
    print("⚠️ produto.txt não encontrado (opcional)")

GUILD_ID = 1472114509068898367
CARGO_MEMBRO = 1472666559049633952
CARGO_CLIENTE = 1472666841515032676

CANAL_CARRINHOS = 1473180070851117108
CANAL_PAGOS = 1473182832225554554

MEU_ID = 736643333840961547
CARGO_ADMIN = 1472666559049633952

carrinhos_ativos = {}

# ===============================
# LOCKS PARA THREAD SAFETY
# ===============================
webhook_lock = threading.Lock()
estoque_lock = threading.Lock()

# ===============================
# SISTEMA DE PAGAMENTOS PROCESSADOS
# ===============================

def carregar_pagamentos_processados():
    if os.path.exists(ARQUIVO_PAGAMENTOS_PROCESSADOS):
        with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'r', encoding='utf-8') as f:
            try:
                return set(json.load(f))
            except:
                return set()
    return set()

def salvar_pagamentos_processados(pagamentos):
    with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'w', encoding='utf-8') as f:
        json.dump(list(pagamentos), f, indent=2)

pagamentos_processados = carregar_pagamentos_processados()
print(f"🔒 {len(pagamentos_processados)} pagamentos já processados")

# ===============================
# SISTEMA DE ESTOQUE
# ===============================

def carregar_estoque():
    if os.path.exists(ARQUIVO_ESTOQUE_JSON):
        with open(ARQUIVO_ESTOQUE_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        estoque_vazio = {}
        salvar_estoque(estoque_vazio)
        return estoque_vazio

def salvar_estoque(estoque):
    with open(ARQUIVO_ESTOQUE_JSON, 'w', encoding='utf-8') as f:
        json.dump(estoque, f, indent=2, ensure_ascii=False)

estoque_disponivel = carregar_estoque()
print(f"📦 Estoque carregado")

# ===============================
# SISTEMA DE GERENCIAMENTO DE PRODUTOS
# ===============================

def carregar_produtos():
    if os.path.exists(ARQUIVO_PRODUTOS_JSON):
        with open(ARQUIVO_PRODUTOS_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        produtos_vazio = {}
        salvar_produtos(produtos_vazio)
        return produtos_vazio

def salvar_produtos(produtos):
    with open(ARQUIVO_PRODUTOS_JSON, 'w', encoding='utf-8') as f:
        json.dump(produtos, f, indent=2, ensure_ascii=False)

produtos_disponiveis = carregar_produtos()
print(f"📦 {len(produtos_disponiveis)} produtos carregados")

# ===============================
# MERCADO PAGO
# ===============================
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    try:
        preco_formatado = round(float(preco), 2)
        payment_data = {
            "transaction_amount": preco_formatado,
            "description": f"Compra: {nome_produto}"[:60],
            "payment_method_id": "pix",
            "payer": {
                "email": f"c_{user_id}@cliente.com",
                "first_name": "Cliente",
                "last_name": str(user_id)
            },
            "external_reference": f"{produto_id}_{user_id}_{int(time.time())}",
            "installments": 1
        }
        if WEBHOOK_URL and WEBHOOK_URL.startswith("https"):
            payment_data["notification_url"] = WEBHOOK_URL
        
        result = sdk.payment().create(payment_data)
        status_code = result.get("status")
        response_data = result.get("response")

        if status_code in [200, 201]:
            payment = response_data
            pix_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
            return {
                "qr_code": pix_data.get("qr_code"),
                "qr_code_base64": pix_data.get("qr_code_base64"),
                "expiration": payment.get("date_of_expiration"),
                "produto": nome_produto,
                "preco": preco_formatado,
                "payment_id": payment.get("id"),
                "produto_id": produto_id
            }
        return None
    except Exception as e:
        print(f"❌ ERRO PIX: {e}")
        return None

# ===============================
# FUNÇÕES DE ESTOQUE E ENTREGA
# ===============================

def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            return None
        if variacao_nome:
            if variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
                itens = estoque_disponivel[produto_id]["variacoes"][variacao_nome]
                if itens:
                    item = itens.pop(0)
                    salvar_estoque(estoque_disponivel)
                    return item
            return None
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens:
            item = itens.pop(0)
            salvar_estoque(estoque_disponivel)
            return item
        return None

def verificar_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return 0
        if variacao_nome and variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
            return len(estoque_disponivel[produto_id]["variacoes"][variacao_nome])
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# LOGS
# ===============================

async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal: return None
        embed = discord.Embed(title="🛒 NOVO CARRINHO ATIVO", color=0xffaa00, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        mensagem = await canal.send(embed=embed)
        carrinhos_ativos[str(pagamento_id)] = {"canal": canal.id, "mensagem_id": mensagem.id, "usuario": user.id, "produto": produto_nome}
        return mensagem
    except: return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos: return
        embed = discord.Embed(title="✅ PAGAMENTO CONFIRMADO", color=0x00ff88, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        if item_entregue: embed.add_field(name="🔐 Item Entregue", value=f"```{item_entregue}```", inline=False)
        await canal_pagos.send(embed=embed)
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    emb_aprovado = discord.Embed(title="✅ PAGAMENTO APROVADO", color=0x00ff88, timestamp=datetime.now())
                    emb_aprovado.add_field(name="Cliente", value=user.mention, inline=True)
                    emb_aprovado.add_field(name="Produto", value=produto_nome, inline=True)
                    if item_entregue: emb_aprovado.add_field(name="🔐 Item Entregue", value=f"```{item_entregue}```", inline=False)
                    await msg.edit(embed=emb_aprovado)
                except: pass
            del carrinhos_ativos[str(pagamento_id)]
    except: pass

# ===============================
# DISCORD BOT
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
    async def on_ready(self):
        print(f"🟢 Logado como {self.user}")

bot = Bot()

class CopiarPIXView(discord.ui.View):
    def __init__(self, codigo_pix: str):
        super().__init__(timeout=300)
        self.codigo_pix = codigo_pix
    @discord.ui.button(label="📋 Copiar código PIX", style=discord.ButtonStyle.primary)
    async def copiar_pix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"```{self.codigo_pix}```", ephemeral=True)

class VariacoesView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list):
        super().__init__(timeout=300)
        self.produto_id, self.produto_nome, self.variacoes = produto_id, produto_nome, variacoes
        options = [discord.SelectOption(label=v["nome"], description=f"R$ {v['preco']:.2f}", value=str(i)) for i, v in enumerate(variacoes)]
        select = discord.ui.Select(placeholder="Escolha uma opção...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            indice = int(interaction.data["values"][0])
            variacao = self.variacoes[indice]
            if verificar_estoque(self.produto_id, variacao["nome"]) == 0:
                return await interaction.followup.send("❌ Esgotado!", ephemeral=True)
            pix = criar_pagamento_pix_com_preco(interaction.user.id, f"{self.produto_id}_{variacao['nome']}", variacao["preco"], f"{self.produto_nome} - {variacao['nome']}")
            if not pix: return await interaction.followup.send("❌ Erro PIX", ephemeral=True)
            await log_carrinho_ativo(interaction.user, pix['produto'], pix['preco'], pix['payment_id'])
            emb = discord.Embed(title="🧾 PAGAMENTO PIX", description=f"**Produto:** {pix['produto']}\n**Valor:** R$ {pix['preco']:.2f}", color=0x00ff88)
            img = BytesIO(base64.b64decode(pix["qr_code_base64"]))
            await interaction.user.send(embed=emb, file=discord.File(fp=img, filename="qrcode.png"), view=CopiarPIXView(pix["qr_code"]))
            await interaction.followup.send("📨 Enviado no privado!", ephemeral=True)
        except: await interaction.followup.send("❌ Erro", ephemeral=True)

# ===============================
# COMANDOS SLASH (Com Defer)
# ===============================

@bot.tree.command(name="add_estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID: return await interaction.followup.send("❌")
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    with estoque_lock:
        if produto_id not in estoque_disponivel: estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
        if variacao:
            if variacao not in estoque_disponivel[produto_id]["variacoes"]: estoque_disponivel[produto_id]["variacoes"][variacao] = []
            estoque_disponivel[produto_id]["variacoes"][variacao].extend(novos)
        else: estoque_disponivel[produto_id]["itens"].extend(novos)
        salvar_estoque(estoque_disponivel)
    await interaction.followup.send(f"✅ {len(novos)} itens adicionados.")

@bot.tree.command(name="ver_estoque")
async def ver_estoque(interaction: discord.Interaction, produto_id: str, variacao: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID: return await interaction.followup.send("❌")
    itens = estoque_disponivel.get(produto_id, {}).get("variacoes", {}).get(variacao, []) if variacao else estoque_disponivel.get(produto_id, {}).get("itens", [])
    if not itens: return await interaction.followup.send("Estoque vazio.", ephemeral=True)
    txt = "\n".join([f"**{i}** - `{it}`" for i, it in enumerate(itens[:20])])
    await interaction.followup.send(embed=discord.Embed(title=f"📦 ESTOQUE - {produto_id}", description=txt), ephemeral=True)

@bot.tree.command(name="remover_estoque")
async def remover_estoque(interaction: discord.Interaction, produto_id: str, indice: int, variacao: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID: return await interaction.followup.send("❌")
    try:
        with estoque_lock:
            l = estoque_disponivel[produto_id]["variacoes"][variacao] if variacao else estoque_disponivel[produto_id]["itens"]
            l.pop(indice); salvar_estoque(estoque_disponivel)
        await interaction.followup.send("✅ Removido.", ephemeral=True)
    except: await interaction.followup.send("❌ Erro.", ephemeral=True)

@bot.tree.command(name="add_variacao")
async def add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID: return await interaction.followup.send("❌")
    if produto_id not in produtos_disponiveis: return await interaction.followup.send("❌")
    if "variacoes" not in produtos_disponiveis[produto_id]: produtos_disponiveis[produto_id]["variacoes"] = []
    produtos_disponiveis[produto_id]["variacoes"].append({"nome": nome, "preco": preco})
    salvar_produtos(produtos_disponiveis)
    await interaction.followup.send(f"✅ Variação {nome} adicionada.", ephemeral=True)

@bot.tree.command(name="configurar")
async def configurar(interaction: discord.Interaction, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID: return await interaction.followup.send("❌")
    p = produtos_disponiveis.get(produto_id)
    if not p: return await interaction.followup.send("❌", ephemeral=True)
    
    class ComprarView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            if p.get("variacoes"):
                self.add_item(discord.ui.Button(label="Ver Opções", style=discord.ButtonStyle.danger, custom_id=f"btn_{produto_id}"))
        @discord.ui.button(label="Comprar", style=discord.ButtonStyle.danger, emoji="🛒", custom_id="btn_compra_simples")
        async def comprar(self, i: discord.Interaction, b: discord.ui.Button):
            await i.response.defer(ephemeral=True)
            if p.get("variacoes"): return await i.followup.send(view=VariacoesView(produto_id, p["nome"], p["variacoes"]), ephemeral=True)
            if verificar_estoque(produto_id) == 0: return await i.followup.send("❌ Esgotado!", ephemeral=True)
            pix = criar_pagamento_pix_com_preco(i.user.id, produto_id, p["preco"], p["nome"])
            if not pix: return await i.followup.send("❌ Erro PIX", ephemeral=True)
            await log_carrinho_ativo(i.user, pix['produto'], pix['preco'], pix['payment_id'])
            emb = discord.Embed(title="🧾 PAGAMENTO PIX", description=f"**Produto:** {pix['produto']}\n**Valor:** R$ {pix['preco']:.2f}", color=0x00ff88)
            img = BytesIO(base64.b64decode(pix["qr_code_base64"]))
            await i.user.send(embed=emb, file=discord.File(fp=img, filename="qrcode.png"), view=CopiarPIXView(pix["qr_code"]))
            await i.followup.send("📨 Enviado no privado!", ephemeral=True)

    emb = discord.Embed(title=p["nome"], description=p["descricao"], color=0x2b2d31)
    if p.get("imagem"): emb.set_image(url=p["imagem"])
    await interaction.channel.send(embed=emb, view=ComprarView())
    await interaction.followup.send("✅ Vitrine configurada.", ephemeral=True)

# ===============================
# FLASK WEBHOOK
# ===============================
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or request.form.to_dict()
    pid = data.get('data', {}).get('id') or data.get('id') or request.args.get('id')
    if not pid: return "OK", 200
    
    with webhook_lock:
        if str(pid) in pagamentos_processados: return "OK", 200
        try:
            res = sdk.payment().get(pid)
            if res["status"] == 200 and res["response"]["status"] == "approved":
                pay = res["response"]; ref = pay.get("external_reference", "")
                if ref:
                    partes = ref.split('_')
                    if len(partes) >= 3:
                        u_id = int(partes[-2]); p_id = partes[0]
                        v_nome = partes[1] if len(partes) == 4 else None
                        item = entregar_do_estoque(p_id, v_nome)
                        if item:
                            async def deliver():
                                user = bot.get_user(u_id) or await bot.fetch_user(u_id)
                                try:
                                    await user.send(f"✅ **Pagamento confirmado!**\n\n📦 **{p_id}**\n🔐 **Seu produto:**\n```{item}```")
                                    await log_pagamento_confirmado(user, p_id, pay.get('transaction_amount', 0), pid, item)
                                except: pass
                            asyncio.run_coroutine_threadsafe(deliver(), bot.loop)
                            pagamentos_processados.add(str(pid))
                            salvar_pagamentos_processados(pagamentos_processados)
        except: pass
    return "OK", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    bot.run(DISCORD_TOKEN)

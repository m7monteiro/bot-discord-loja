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
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
MP_ACCESS_TOKEN = os.environ["MP_ACCESS_TOKEN"]
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

MEU_ID = 1439411460378726530
CARGO_ADMIN = 1472666559049633952

carrinhos_ativos = {}

# ===============================
# LOCKS PARA THREAD SAFETY (CORREÇÃO ANTI-DUPLICAÇÃO)
# ===============================
webhook_lock = threading.Lock()
estoque_lock = threading.Lock()

# ===============================
# SISTEMA DE PAGAMENTOS PROCESSADOS (PERSISTENTE)
# ===============================

def carregar_pagamentos_processados():
    if os.path.exists(ARQUIVO_PAGAMENTOS_PROCESSADOS):
        with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'r', encoding='utf-8') as f:
            return set(json.load(f))
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
    payment_data = {
        "transaction_amount": preco,
        "description": nome_produto,
        "payment_method_id": "pix",
        "payer": {"email": f"cliente_{user_id}@temp.com"},
        "external_reference": f"{produto_id}_{user_id}_{int(time.time())}",
        "notification_url": WEBHOOK_URL
    }
    
    try:
        result = sdk.payment().create(payment_data)
        
        if result["status"] == 201:
            payment = result["response"]
            pix_data = payment["point_of_interaction"]["transaction_data"]
            
            return {
                "qr_code": pix_data["qr_code"],
                "qr_code_base64": pix_data["qr_code_base64"],
                "expiration": payment["date_of_expiration"],
                "produto": nome_produto,
                "preco": preco,
                "payment_id": payment["id"],
                "produto_id": produto_id
            }
    except Exception as e:
        print(f"❌ Erro PIX: {e}")
        return None
    
    return None

# ===============================
# FUNÇÃO PARA ENTREGAR PRODUTO DO ESTOQUE (THREAD SAFE)
# ===============================

def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            print(f"❌ Produto {produto_id} não encontrado no estoque")
            return None
        
        if variacao_nome:
            if variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
                itens = estoque_disponivel[produto_id]["variacoes"][variacao_nome]
                if itens and len(itens) > 0:
                    item = itens.pop(0)
                    salvar_estoque(estoque_disponivel)
                    print(f"✅ Entregue da variação {variacao_nome}: {item}")
                    return item
                else:
                    print(f"⚠️ Estoque vazio para variação {variacao_nome}")
                    return None
            else:
                print(f"⚠️ Variação {variacao_nome} não encontrada")
                return None
        
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens and len(itens) > 0:
            item = itens.pop(0)
            salvar_estoque(estoque_disponivel)
            print(f"✅ Entregue do estoque geral: {item}")
            return item
        
        print(f"⚠️ Estoque vazio para {produto_id}")
        return None

def verificar_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            return 0
        
        if variacao_nome and variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
            return len(estoque_disponivel[produto_id]["variacoes"][variacao_nome])
        
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# FUNÇÕES DE LOG
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal:
            return None
        
        embed = discord.Embed(
            title="🛒 NOVO CARRINHO ATIVO",
            color=0xffaa00,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Horário", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="⏳ Aguardando pagamento...")
        
        mensagem = await canal.send(embed=embed)
        
        carrinhos_ativos[str(pagamento_id)] = {
            "canal": canal.id,
            "mensagem_id": mensagem.id,
            "usuario": user.id,
            "produto": produto_nome
        }
        
        return mensagem
    except Exception as e:
        print(f"❌ Erro log carrinho: {e}")
        return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos:
            return
        
        embed = discord.Embed(
            title="✅ PAGAMENTO CONFIRMADO",
            color=0x00ff88,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Horário", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="🎉 Produto entregue com sucesso!")
        
        await canal_pagos.send(embed=embed)
        
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    await msg.delete()
                except:
                    pass
            del carrinhos_ativos[str(pagamento_id)]
    except Exception as e:
        print(f"❌ Erro log pagos: {e}")

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

bot = Bot()

# ===============================
# CLASSE DO BOTÃO DE COPIAR PIX
# ===============================
class CopiarPIXView(discord.ui.View):
    def __init__(self, codigo_pix: str):
        super().__init__(timeout=300)
        self.codigo_pix = codigo_pix

    @discord.ui.button(label="📋 Copiar código PIX", style=discord.ButtonStyle.primary)
    async def copiar_pix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"```{self.codigo_pix}```", 
            ephemeral=True
        )

# ===============================
# CLASSE DO MENU DE VARIAÇÕES
# ===============================
class VariacoesView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list):
        super().__init__(timeout=None)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        
        options = []
        for i, v in enumerate(variacoes):
            options.append(
                discord.SelectOption(
                    label=v["nome"][:100],
                    value=str(i),
                    description=f"R$ {v['preco']:.2f}"[:100]
                )
            )
        
        select = discord.ui.Select(
            placeholder="Selecione uma opção...",
            options=options
        )
        select.callback = self.select_callback
        self.add_item(select)
        self.variacoes = variacoes
    
    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        indice = int(interaction.data["values"][0])
        variacao = self.variacoes[indice]
        user = interaction.user
        
        try:
            qtd_estoque = verificar_estoque(self.produto_id, variacao["nome"])
            produto_info = produtos_disponiveis[self.produto_id]
            
            if qtd_estoque == 0 and produto_info.get("tipo") == "auto":
                await interaction.followup.send(
                    f"❌ **{variacao['nome']} está esgotado!** Aguarde reposição.",
                    ephemeral=True
                )
                return
            
            pix_data = criar_pagamento_pix_com_preco(
                user.id,
                f"{self.produto_id}_{variacao['nome']}",
                variacao["preco"],
                f"{self.produto_nome} - {variacao['nome']}"
            )
            
            if not pix_data:
                await interaction.followup.send("❌ Erro ao gerar pagamento.", ephemeral=True)
                return
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO PIX",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            try:
                expiracao = datetime.fromisoformat(pix_data["expiration"].replace("Z", "+00:00"))
                tempo_restante = expiracao - datetime.now(expiracao.tzinfo)
                minutos = int(tempo_restante.total_seconds() / 60)
                embed_pix.add_field(name="⏰ Expira em", value=f"{minutos} minutos", inline=True)
            except:
                embed_pix.add_field(name="⏰ Expira em", value="15 minutos", inline=True)
            
            embed_pix.set_footer(text="Você receberá o produto aqui assim que o pagamento for confirmado!")
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
            
            await interaction.followup.send("📨 Informações enviadas no seu privado!", ephemeral=True)
            
        except Exception as e:
            print(f"❌ Erro variação: {e}")
            await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)

# ===============================
# COMANDOS DE ADMIN - ESTOQUE
# ===============================

@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar item ao estoque")
@app_commands.describe(
    produto_id="ID do produto",
    item="Item a adicionar (senha, código, etc.)",
    variacao="Nome da variação (opcional)"
)
async def add_estoque(
    interaction: discord.Interaction,
    produto_id: str,
    item: str,
    variacao: str = None
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
        
        if variacao:
            if variacao not in estoque_disponivel[produto_id].get("variacoes", {}):
                if "variacoes" not in estoque_disponivel[produto_id]:
                    estoque_disponivel[produto_id]["variacoes"] = {}
                estoque_disponivel[produto_id]["variacoes"][variacao] = []
            estoque_disponivel[produto_id]["variacoes"][variacao].append(item)
            qtd = len(estoque_disponivel[produto_id]["variacoes"][variacao])
        else:
            if "itens" not in estoque_disponivel[produto_id]:
                estoque_disponivel[produto_id]["itens"] = []
            estoque_disponivel[produto_id]["itens"].append(item)
            qtd = len(estoque_disponivel[produto_id]["itens"])
        
        salvar_estoque(estoque_disponivel)
    
    produto_nome = produtos_disponiveis[produto_id]['nome']
    variacao_texto = f" (variação: {variacao})" if variacao else ""
    
    await interaction.response.send_message(
        f"✅ **Item adicionado ao estoque!**\n\n"
        f"📦 Produto: {produto_nome}{variacao_texto}\n"
        f"🔐 Item: `{item}`\n"
        f"📊 Total em estoque: {qtd} itens",
        ephemeral=True
    )

@bot.tree.command(name="remover_estoque", description="[ADMIN] Remover item específico do estoque")
@app_commands.describe(
    produto_id="ID do produto",
    indice="Índice do item (use /ver_estoque para ver)"
)
async def remover_estoque(
    interaction: discord.Interaction,
    produto_id: str,
    indice: int
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in estoque_disponivel:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    with estoque_lock:
        itens = estoque_disponivel[produto_id].get("itens", [])
        if indice < 0 or indice >= len(itens):
            await interaction.response.send_message(f"❌ Índice inválido! Use 0 a {len(itens)-1}", ephemeral=True)
            return
        
        removido = itens.pop(indice)
        salvar_estoque(estoque_disponivel)
    
    await interaction.response.send_message(
        f"✅ **Item removido do estoque!**\n\n"
        f"📦 Produto: {produtos_disponiveis[produto_id]['nome']}\n"
        f"🔐 Item removido: `{removido}`",
        ephemeral=True
    )

@bot.tree.command(name="ver_estoque", description="[ADMIN] Ver estoque disponível")
@app_commands.describe(produto_id="ID do produto")
async def ver_estoque(interaction: discord.Interaction, produto_id: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis[produto_id]
    
    if produto_id not in estoque_disponivel:
        estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
    
    itens = estoque_disponivel[produto_id].get("itens", [])
    
    if not itens:
        await interaction.response.send_message(f"📦 **{produto['nome']}**\n\nEstoque vazio! Use `/add_estoque` para adicionar.", ephemeral=True)
        return
    
    descricao = ""
    for i, item in enumerate(itens):
        descricao += f"**{i}** - `{item}`\n"
    
    embed = discord.Embed(
        title=f"📦 ESTOQUE - {produto['nome']}",
        description=descricao,
        color=0x2b2d31
    )
    embed.set_footer(text=f"Total: {len(itens)} itens | Use /remover_estoque com o índice")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ===============================
# COMANDOS DE ADMIN - VARIAÇÕES
# ===============================

@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação a um produto")
@app_commands.describe(
    produto_id="ID do produto",
    nome="Nome da variação (ex: Completo, Apenas Conta, Premium)",
    preco="Preço da variação em R$"
)
async def add_variacao(
    interaction: discord.Interaction,
    produto_id: str,
    nome: str,
    preco: float
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    if "variacoes" not in produtos_disponiveis[produto_id]:
        produtos_disponiveis[produto_id]["variacoes"] = []
    
    produtos_disponiveis[produto_id]["variacoes"].append({
        "nome": nome,
        "preco": preco
    })
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(
        f"✅ Variação adicionada!\n\n"
        f"📦 Produto: {produtos_disponiveis[produto_id]['nome']}\n"
        f"🎮 Opção: {nome}\n"
        f"💰 Preço: R$ {preco:.2f}",
        ephemeral=True
    )

@bot.tree.command(name="listar_variacoes", description="[ADMIN] Listar variações de um produto")
@app_commands.describe(produto_id="ID do produto")
async def listar_variacoes(interaction: discord.Interaction, produto_id: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis[produto_id]
    variacoes = produto.get("variacoes", [])
    
    if not variacoes:
        await interaction.response.send_message(f"📦 **{produto['nome']}**\n\nNenhuma variação cadastrada.\n\nUse `/add_variacao` para criar!", ephemeral=True)
        return
    
    descricao = ""
    for i, v in enumerate(variacoes):
        descricao += f"**{i}** - {v['nome']} - R$ {v['preco']:.2f}\n"
    
    embed = discord.Embed(
        title=f"📦 VARIAÇÕES - {produto['nome']}",
        description=descricao,
        color=0x2b2d31
    )
    embed.set_footer(text="Use /editar_variacao ou /remover_variacao com o índice")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="editar_variacao", description="[ADMIN] Editar nome ou preço de uma variação")
@app_commands.describe(
    produto_id="ID do produto",
    indice="Índice da variação (use /listar_variacoes para ver)",
    novo_nome="Novo nome da variação (opcional)",
    novo_preco="Novo preço da variação (opcional)"
)
async def editar_variacao(
    interaction: discord.Interaction,
    produto_id: str,
    indice: int,
    novo_nome: str = None,
    novo_preco: float = None
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
    if indice < 0 or indice >= len(variacoes):
        await interaction.response.send_message(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
        return
    
    variacao = variacoes[indice]
    mensagem = f"✅ Variação editada!\n\n📦 Produto: {produtos_disponiveis[produto_id]['nome']}\n"
    
    if novo_nome:
        mensagem += f"📝 Nome: {variacao['nome']} → {novo_nome}\n"
        variacao["nome"] = novo_nome
    
    if novo_preco:
        mensagem += f"💰 Preço: R$ {variacao['preco']:.2f} → R$ {novo_preco:.2f}\n"
        variacao["preco"] = novo_preco
    
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(mensagem, ephemeral=True)

@bot.tree.command(name="remover_variacao", description="[ADMIN] Remover variação de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    indice="Número da variação (use /listar_variacoes para ver)"
)
async def remover_variacao(
    interaction: discord.Interaction,
    produto_id: str,
    indice: int
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
    if indice < 0 or indice >= len(variacoes):
        await interaction.response.send_message(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
        return
    
    removida = variacoes.pop(indice)
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(
        f"✅ Variação removida!\n\n"
        f"📦 Produto: {produtos_disponiveis[produto_id]['nome']}\n"
        f"🎮 Opção removida: {removida['nome']}\n"
        f"💰 Preço: R$ {removida['preco']:.2f}",
        ephemeral=True
    )

# ===============================
# COMANDOS DE CLIENTE
# ===============================
@bot.tree.command(name="produtos", description="Ver todos os produtos disponíveis")
async def listar_produtos(interaction: discord.Interaction):
    if not produtos_disponiveis:
        await interaction.response.send_message("📦 **Nenhum produto cadastrado ainda!**\n\nUse `/criar_produto` para adicionar.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🛒 M7 STORE - PRODUTOS",
        description="Use `/comprar [id]` para adquirir qualquer produto!",
        color=0x2b2d31,
        timestamp=datetime.now()
    )
    
    for key, prod in produtos_disponiveis.items():
        tipo_texto = "Automática" if prod.get('tipo') == 'auto' else "Manual"
        qtd_variacoes = len(prod.get("variacoes", []))
        qtd_estoque = verificar_estoque(key)
        estoque_texto = f" | {qtd_estoque} em estoque" if prod.get('tipo') == 'auto' else ""
        variacoes_texto = f" | {qtd_variacoes} opções" if qtd_variacoes > 0 else ""
        
        embed.add_field(
            name=f"📦 {prod['nome']}",
            value=f"💰 Preço: R$ {prod['preco']:.2f}\n"
                  f"📝 Entrega: {tipo_texto}{variacoes_texto}{estoque_texto}\n"
                  f"🆔 ID: `{key}`",
            inline=False
        )
    
    embed.set_footer(text="M7 STORE")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="loja", description="🛒 Ver todos os produtos da loja")
async def mostrar_loja(interaction: discord.Interaction):
    if not produtos_disponiveis:
        await interaction.response.send_message("📦 **Nenhum produto cadastrado ainda!**\n\nUse `/criar_produto` para adicionar.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="M7 STORE",
        description="Selecione um produto abaixo",
        color=0x2b2d31,
        timestamp=datetime.now()
    )
    
    for key, prod in produtos_disponiveis.items():
        desc_formatada = prod.get('descricao', 'Sem descrição')
        qtd_variacoes = len(prod.get("variacoes", []))
        qtd_estoque = verificar_estoque(key)
        estoque_texto = f"\n📊 Estoque: {qtd_estoque} unidades" if prod.get('tipo') == 'auto' else ""
        variacoes_texto = f"\n🎮 {qtd_variacoes} opções disponíveis" if qtd_variacoes > 0 else ""
        
        embed.add_field(
            name=f"📦 {prod['nome']}",
            value=f"{desc_formatada}\n\n💰 Preço: R$ {prod['preco']:.2f}{variacoes_texto}{estoque_texto}\n🆔 ID: `{key}`",
            inline=False
        )
    
    embed.set_footer(text="M7 STORE - Use /comprar para adquirir!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ===============================
# COMANDOS PARA CANAIS INDIVIDUAIS
# ===============================

async def criar_embed_produto(produto_id: str, produto_info: dict):
    imagem_url = produto_info.get('imagem', '')
    qtd_variacoes = len(produto_info.get("variacoes", []))
    qtd_estoque = verificar_estoque(produto_id)
    estoque_texto = f"\n📊 Estoque: {qtd_estoque} unidades" if produto_info.get('tipo') == 'auto' else ""
    variacoes_texto = f"\n🎮 {qtd_variacoes} opções disponíveis" if qtd_variacoes > 0 else ""
    
    embed = discord.Embed(
        title=f"{produto_info['nome'].upper()}",
        description=produto_info.get('descricao', 'Sem descrição') + variacoes_texto + estoque_texto,
        color=0x2b2d31,
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="PREÇO",
        value=f"R$ {produto_info['preco']:.2f}",
        inline=False
    )
    
    embed.set_footer(text="M7 STORE - Clique no botão abaixo para comprar!")
    
    if imagem_url and imagem_url != "":
        embed.set_thumbnail(url=imagem_url)
    
    return embed

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list = None):
        super().__init__(timeout=None)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes or []
    
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success, custom_id="btn_comprar")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        if self.variacoes and len(self.variacoes) > 0:
            view = VariacoesView(self.produto_id, self.produto_nome, self.variacoes)
            await interaction.followup.send(
                f"📦 **{self.produto_nome}**\n\nSelecione a opção desejada:",
                view=view,
                ephemeral=True
            )
            return
        
        user = interaction.user
        
        try:
            produto_info = produtos_disponiveis[self.produto_id]
            
            qtd_estoque = verificar_estoque(self.produto_id)
            if qtd_estoque == 0 and produto_info.get("tipo") == "auto":
                await interaction.followup.send("❌ **Produto esgotado!** Aguarde reposição.", ephemeral=True)
                return
            
            pix_data = criar_pagamento_pix_com_preco(user.id, self.produto_id, produto_info["preco"], self.produto_nome)
            
            if not pix_data:
                await interaction.followup.send("❌ Erro ao gerar pagamento.", ephemeral=True)
                return
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO PIX",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            try:
                expiracao = datetime.fromisoformat(pix_data["expiration"].replace("Z", "+00:00"))
                tempo_restante = expiracao - datetime.now(expiracao.tzinfo)
                minutos = int(tempo_restante.total_seconds() / 60)
                embed_pix.add_field(name="⏰ Expira em", value=f"{minutos} minutos", inline=True)
            except:
                embed_pix.add_field(name="⏰ Expira em", value="15 minutos", inline=True)
            
            embed_pix.set_footer(text="Você receberá o produto aqui assim que o pagamento for confirmado!")
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
            await interaction.followup.send("📨 Informações enviadas no seu privado!", ephemeral=True)
            
        except Exception as e:
            print(f"❌ Erro: {e}")
            await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)

@bot.tree.command(name="configurar_produto", description="[ADMIN] Criar/atualizar canal de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    nome_canal="Nome do canal"
)
async def configurar_produto(
    interaction: discord.Interaction,
    produto_id: str,
    nome_canal: str
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    produto_info = produtos_disponiveis[produto_id]
    
    canal_existente = None
    for channel in interaction.guild.channels:
        if channel.name == nome_canal:
            canal_existente = channel
            break
    
    if not canal_existente:
        categoria = None
        for cat in interaction.guild.categories:
            if cat.name == "🛒 PRODUTOS":
                categoria = cat
                break
        
        if not categoria:
            categoria = await interaction.guild.create_category("🛒 PRODUTOS")
        
        canal = await interaction.guild.create_text_channel(
            nome_canal,
            category=categoria,
            topic=f"Venda de {produto_info['nome']} - Preço: R$ {produto_info['preco']:.2f}"
        )
        
        await canal.set_permissions(interaction.guild.default_role, send_messages=False)
        await canal.set_permissions(interaction.guild.me, send_messages=True, read_messages=True)
        
        mensagem = f"✅ Canal criado! #{nome_canal}"
    else:
        canal = canal_existente
        async for msg in canal.history(limit=50):
            if msg.author == bot.user:
                await msg.delete()
        mensagem = f"✅ Canal atualizado! #{nome_canal}"
    
    embed = await criar_embed_produto(produto_id, produto_info)
    view = ProdutoCompraView(produto_id, produto_info['nome'], produto_info.get("variacoes", []))
    
    await canal.send(embed=embed, view=view)
    await interaction.followup.send(mensagem, ephemeral=True)

@bot.tree.command(name="atualizar_produto", description="[ADMIN] Atualizar produto e canal automaticamente")
@app_commands.describe(
    produto_id="ID do produto",
    novo_nome="Novo nome (opcional)",
    novo_preco="Novo preço (opcional)",
    nova_descricao="Nova descrição (opcional)",
    nova_imagem="Nova URL da imagem (opcional)"
)
async def atualizar_produto(
    interaction: discord.Interaction,
    produto_id: str,
    novo_nome: str = None,
    novo_preco: float = None,
    nova_descricao: str = None,
    nova_imagem: str = None
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    produto = produtos_disponiveis[produto_id]
    mensagem = f"✅ Produto atualizado!\n\n"
    
    if novo_nome:
        mensagem += f"📝 Nome: {produto['nome']} → {novo_nome}\n"
        produto["nome"] = novo_nome
    
    if novo_preco:
        mensagem += f"💰 Preço: R$ {produto['preco']:.2f} → R$ {novo_preco:.2f}\n"
        produto["preco"] = novo_preco
    
    if nova_descricao:
        mensagem += f"📄 Descrição atualizada\n"
        produto["descricao"] = nova_descricao
    
    if nova_imagem:
        mensagem += f"🖼️ Imagem atualizada\n"
        produto["imagem"] = nova_imagem
    
    salvar_produtos(produtos_disponiveis)
    
    canal_atualizado = False
    for channel in interaction.guild.channels:
        if channel.name == produto_id or channel.name == produto_id.lower():
            async for msg in channel.history(limit=50):
                if msg.author == bot.user:
                    await msg.delete()
            
            embed = await criar_embed_produto(produto_id, produto)
            view = ProdutoCompraView(produto_id, produto['nome'], produto.get("variacoes", []))
            await channel.send(embed=embed, view=view)
            canal_atualizado = True
            break
    
    if canal_atualizado:
        mensagem += f"\n✅ Canal #{produto_id} atualizado automaticamente!"
    else:
        mensagem += f"\n⚠️ Canal não encontrado. Use `/configurar_produto` para criar."
    
    await interaction.followup.send(mensagem, ephemeral=True)

@bot.tree.command(name="sincronizar_canal", description="[ADMIN] Forçar atualização do canal do produto")
@app_commands.describe(produto_id="ID do produto")
async def sincronizar_canal(interaction: discord.Interaction, produto_id: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    canal_atualizado = False
    for channel in interaction.guild.channels:
        if channel.name == produto_id or channel.name == produto_id.lower():
            async for msg in channel.history(limit=50):
                if msg.author == bot.user:
                    await msg.delete()
            
            produto = produtos_disponiveis[produto_id]
            embed = await criar_embed_produto(produto_id, produto)
            view = ProdutoCompraView(produto_id, produto['nome'], produto.get("variacoes", []))
            await channel.send(embed=embed, view=view)
            canal_atualizado = True
            break
    
    if canal_atualizado:
        await interaction.followup.send(f"✅ Canal #{produto_id} sincronizado!", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ Canal para {produto_id} não encontrado!", ephemeral=True)

@bot.tree.command(name="editar_imagem", description="[ADMIN] Mudar a imagem do produto")
@app_commands.describe(
    produto_id="ID do produto",
    url_imagem="URL da imagem (ex: https://...png)"
)
async def editar_imagem(interaction: discord.Interaction, produto_id: str, url_imagem: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    produtos_disponiveis[produto_id]["imagem"] = url_imagem
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(
        f"✅ Imagem atualizada!\n🖼️ Nova imagem: {url_imagem}\n\n💡 Use `/sincronizar_canal {produto_id}` para aplicar.",
        ephemeral=True
    )

# ===============================
# COMANDOS DE ADMIN (GERENCIAMENTO BASE)
# ===============================

@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
@app_commands.describe(
    id="ID único do produto",
    nome="Nome do produto",
    preco="Preço em R$",
    descricao="Descrição do produto",
    tipo="Tipo: auto or manual"
)
async def criar_produto(
    interaction: discord.Interaction,
    id: str,
    nome: str,
    preco: float,
    descricao: str,
    tipo: str = "auto"
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if id in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto com ID `{id}` já existe!", ephemeral=True)
        return
    
    if tipo not in ["auto", "manual"]:
        await interaction.response.send_message("❌ Tipo deve ser `auto` ou `manual`", ephemeral=True)
        return
    
    produtos_disponiveis[id] = {
        "nome": nome,
        "preco": preco,
        "descricao": descricao,
        "tipo": tipo,
        "imagem": "",
        "variacoes": []
    }
    salvar_produtos(produtos_disponiveis)
    
    if id not in estoque_disponivel:
        estoque_disponivel[id] = {"itens": [], "variacoes": {}}
        salvar_estoque(estoque_disponivel)
    
    tipo_texto = "🤖 Entrega automática" if tipo == "auto" else "👨‍💼 Entrega manual"
    
    await interaction.response.send_message(
        f"✅ Produto criado!\n\n📦 ID: `{id}`\n📝 Nome: {nome}\n💰 Preço: R$ {preco:.2f}\n🎮 Tipo: {tipo_texto}\n\n💡 Use `/add_estoque` para adicionar itens!\n💡 Use `/add_variacao` para adicionar opções!\n💡 Use `/configurar_produto {id} {id}` para criar o canal!",
        ephemeral=True
    )

@bot.tree.command(name="editar_preco", description="[ADMIN] Alterar preço de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    novo_preco="Novo preço em R$"
)
async def editar_preco(interaction: discord.Interaction, produto_id: str, novo_preco: float):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis[produto_id]
    preco_antigo = produto["preco"]
    produto["preco"] = novo_preco
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(
        f"✅ Preço atualizado!\n📦 Produto: {produto['nome']}\n📉 Antigo: R$ {preco_antigo:.2f}\n📈 Novo: R$ {novo_preco:.2f}",
        ephemeral=True
    )

@bot.tree.command(name="editar_produto", description="[ADMIN] Alterar nome/descrição")
@app_commands.describe(
    produto_id="ID do produto",
    novo_nome="Novo nome (opcional)",
    nova_descricao="Nova descrição (opcional)"
)
async def editar_produto(
    interaction: discord.Interaction, 
    produto_id: str, 
    novo_nome: str = None, 
    nova_descricao: str = None
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis[produto_id]
    mensagem = f"✅ Produto atualizado!\n\n📦 ID: `{produto_id}`\n"
    
    if novo_nome:
        mensagem += f"📝 Nome: {produto['nome']} → {novo_nome}\n"
        produto["nome"] = novo_nome
    
    if nova_descricao:
        mensagem += f"📄 Descrição atualizada\n"
        produto["descricao"] = nova_descricao
    
    salvar_produtos(produtos_disponiveis)
    await interaction.response.send_message(mensagem, ephemeral=True)

@bot.tree.command(name="remover_produto", description="[ADMIN] Remover um produto")
@app_commands.describe(produto_id="ID do produto")
async def remover_produto(interaction: discord.Interaction, produto_id: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis.pop(produto_id)
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(f"✅ Produto removido!\n📦 Removido: {produto['nome']}", ephemeral=True)

@bot.tree.command(name="entregar", description="[ADMIN] Entregar produto manual do estoque")
@app_commands.describe(
    usuario="ID do usuário",
    produto_id="ID do produto",
    indice="Índice do item no estoque (opcional, use /ver_estoque para ver)"
)
async def entregar_produto(
    interaction: discord.Interaction, 
    usuario: str, 
    produto_id: str,
    indice: int = -1
):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id = int(usuario)
        user = await bot.fetch_user(user_id)
        
        if not user:
            await interaction.followup.send("❌ Usuário não encontrado.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send(f"❌ Produto não encontrado!", ephemeral=True)
            return
        
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
            
            itens = estoque_disponivel[produto_id].get("itens", [])
            
            if not itens:
                await interaction.followup.send(f"❌ **Estoque vazio para {produtos_disponiveis[produto_id]['nome']}!**\n\nUse `/add_estoque` para adicionar itens.", ephemeral=True)
                return
            
            if indice == -1:
                item = itens.pop(0)
            else:
                if indice < 0 or indice >= len(itens):
                    await interaction.followup.send(f"❌ Índice inválido! Use 0 a {len(itens)-1} ou /ver_estoque para ver os índices.", ephemeral=True)
                    return
                item = itens.pop(indice)
            
            salvar_estoque(estoque_disponivel)
        
        produto = produtos_disponiveis[produto_id]
        
        await user.send(
            f"🎮 **Sua {produto['nome']} chegou!**\n\n"
            f"```{item}```\n\n"
            "✅ Obrigado pela preferência!"
        )
        
        await interaction.followup.send(f"✅ **{produto['nome']} entregue para {user.name}!**\n🔐 Item: `{item}`\n📊 Restam {len(estoque_disponivel[produto_id].get('itens', []))} itens em estoque.", ephemeral=True)
        
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(
                title="📦 PRODUTO ENTREGUE",
                color=0x3498db,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 Cliente", value=user.mention, inline=True)
            embed.add_field(name="📦 Produto", value=produto['nome'], inline=True)
            embed.add_field(name="🔐 Item", value=f"`{item}`", inline=False)
            embed.set_footer(text=f"Entregue por: {interaction.user.name}")
            await canal_pagos.send(embed=embed)
        
    except ValueError:
        await interaction.followup.send("❌ ID inválido.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="backup", description="[ADMIN] Fazer backup dos produtos")
@app_commands.describe()
async def fazer_backup(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    backup_data = json.dumps(produtos_disponiveis, indent=2, ensure_ascii=False)
    import io
    file = discord.File(io.StringIO(backup_data), filename="backup_produtos.json")
    
    await interaction.response.send_message(
        "✅ Backup realizado! Guarde este arquivo.",
        file=file,
        ephemeral=True
    )

@bot.tree.command(name="2fa", description="Gerar código 2FA a partir da chave")
@app_commands.describe(chave="Sua chave 2FA (ex: 7J64V3P3E77J3LKNUGSZ5QANTLRLTKVL)")
async def gerar_2fa(interaction: discord.Interaction, chave: str):
    """Gera o código 2FA atual a partir da chave fornecida"""
    try:
        chave = chave.strip().upper()
        if len(chave) < 16:
            embed = discord.Embed(
                title="❌ **CHAVE INVÁLIDA**",
                description="A chave deve ter pelo menos 16 caracteres.\nVerifique se você copiou corretamente.",
                color=0xff0000,
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        totp = pyotp.TOTP(chave)
        codigo_atual = totp.now()
        tempo_restante = totp.interval - (int(time.time()) % totp.interval)
        
        embed = discord.Embed(
            title="🔐 **CÓDIGO 2FA GERADO**",
            description="Use o código abaixo para acessar sua conta:",
            color=0x00ff88,
            timestamp=datetime.now()
        )
        embed.add_field(name="📋 **CÓDIGO:**", value=f"```{codigo_atual}```", inline=False)
        embed.add_field(name="⏰ **VÁLIDO POR:**", value=f"{tempo_restante} segundos", inline=True)
        embed.add_field(name="🔄 **EXPIRA EM:**", value=f"{tempo_restante}s", inline=True)
        embed.add_field(name="🔑 **SUA CHAVE:**", value=f"||{chave}||", inline=False)
        embed.set_footer(text="O código expira em 30 segundos. Use /2fa novamente para gerar um novo.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    except Exception as e:
        print(f"❌ Erro no comando 2FA: {e}")
        embed = discord.Embed(
            title="❌ **ERRO AO GERAR CÓDIGO**",
            description="Verifique se a chave 2FA está correta.\n\n**Formato esperado:**\n`7J64V3P3E77J3LKNUGSZ5QANTLRLTKVL`",
            color=0xff0000,
            timestamp=datetime.now()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ===============================
# WEBHOOK (CORRIGIDO - ANTI-DUPLICAÇÃO E ENTREGA SEGURA)
# ===============================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 M7 STORE - Bot está online e funcionando!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    print("=" * 50)
    print("🔔 WEBHOOK ACIONADO!")
    print("=" * 50)
    
    data = request.json if request.is_json else request.form.to_dict()
    print(f"📩 Webhook recebido: {data}")
    
    payment_id = None
    if data and isinstance(data, dict):
        payment_id = data.get('data', {}).get('id') or data.get('id')
    
    if not payment_id:
        payment_id = request.args.get('id') or request.args.get('data.id')
    
    if not payment_id:
        print("❌ Não foi possível extrair o payment_id")
        return "OK", 200
    
    print(f"💰 Payment ID encontrado: {payment_id}")

    with webhook_lock:
        if str(payment_id) in pagamentos_processados:
            print(f"⚠️ Pagamento {payment_id} já foi processado! Ignorando...")
            return "OK", 200
        
        try:
            print(f"🔍 Buscando pagamento {payment_id} no Mercado Pago...")
            payment_response = sdk.payment().get(payment_id)
            
            if payment_response["status"] == 200:
                payment = payment_response["response"]
                
                if payment["status"] == "approved":
                    print("🎉 PAGAMENTO APROVADO!")
                    
                    pagamentos_processados.add(str(payment_id))
                    salvar_pagamentos_processados(pagamentos_processados)
                    
                    ref = payment.get("external_reference", "")
                    if ref:
                        partes = ref.split('_')
                        if len(partes) >= 3:
                            produto_id = partes[0]
                            try:
                                user_id = int(partes[-2])
                                variacao_nome = "_".join(partes[1:-2]) if len(partes) > 3 else None
                            except ValueError:
                                if len(partes) == 4:
                                    variacao_nome = partes[1]
                                    user_id = int(partes[2])
                                else:
                                    variacao_nome = None
                                    user_id = int(partes[1])
                            
                            if user_id == MEU_ID:
                                return "OK", 200
                            
                            user = bot.get_user(user_id)
                            if not user:
                                try:
                                    future = asyncio.run_coroutine_threadsafe(bot.fetch_user(user_id), bot.loop)
                                    user = future.result(timeout=10)
                                except:
                                    user = None
                            
                            if user and produto_id in produtos_disponiveis:
                                produto_info = produtos_disponiveis[produto_id]
                                
                                if produto_info.get("tipo") == "auto":
                                    item = entregar_do_estoque(produto_id, variacao_nome=variacao_nome)
                                    if item:
                                        async def enviar_dm():
                                            try:
                                                await user.send(
                                                    f"✅ **Pagamento confirmado!**\n\n"
                                                    f"📦 **{produto_info['nome']}**\n\n"
                                                    f"🔐 **Seu produto:**\n```{item}```\n\n"
                                                    "✅ Obrigado pela preferência!"
                                                )
                                            except discord.Forbidden:
                                                canal_pagos = bot.get_channel(CANAL_PAGOS)
                                                if canal_pagos:
                                                    await canal_pagos.send(f"⚠️ {user.mention}, seu pagamento de **{produto_info['nome']}** foi aprovado, mas sua DM está fechada! Abra um ticket para receber.")
                                                with estoque_lock:
                                                    if variacao_nome:
                                                        estoque_disponivel[produto_id]["variacoes"][variacao_nome].insert(0, item)
                                                    else:
                                                        estoque_disponivel[produto_id]["itens"].insert(0, item)
                                                    salvar_estoque(estoque_disponivel)
                                        asyncio.run_coroutine_threadsafe(enviar_dm(), bot.loop)
                                    else:
                                        async def avisar_esgotado():
                                            try: await user.send(f"✅ **Pagamento confirmado!**\n\n📦 **{produto_info['nome']}**\n\n⚠️ **Estoque esgotado!** Um administrador irá entregar em breve.")
                                            except: pass
                                        asyncio.run_coroutine_threadsafe(avisar_esgotado(), bot.loop)
                                else:
                                    async def avisar_manual():
                                        try: await user.send(f"✅ **Pagamento aprovado!**\n\n📦 **{produto_info['nome']}**\n\n👨‍💼 Um administrador irá entregar em breve.\n🆔 Pedido: `{payment_id}`")
                                        except: pass
                                    asyncio.run_coroutine_threadsafe(avisar_manual(), bot.loop)
                                
                                asyncio.run_coroutine_threadsafe(log_pagamento_confirmado(user, produto_info["nome"], produto_info["preco"], payment_id), bot.loop)
                else:
                    print(f"⚠️ Pagamento não aprovado. Status: {payment['status']}")
            else:
                print(f"❌ Erro ao buscar pagamento: {payment_response}")
        except Exception as e:
            print(f"❌ ERRO CRÍTICO NO WEBHOOK: {e}")
            if str(payment_id) in pagamentos_processados:
                pagamentos_processados.discard(str(payment_id))
                salvar_pagamentos_processados(pagamentos_processados)
    
    print("=" * 50)
    return "OK", 200

# ===============================
# START
# ===============================
def iniciar_flask():
    print("🌐 Flask online")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

def start_all():
    threading.Thread(target=iniciar_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_all()

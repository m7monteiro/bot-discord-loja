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

if not os.path.exists(ARQUIVO_PRODUTO):
    print("❌ produto.txt não encontrado")
    sys.exit()

GUILD_ID = 1472114509068898367
CARGO_MEMBRO = 1472666559049633952
CARGO_CLIENTE = 1472666841515032676

# IDs dos canais de log
CANAL_CARRINHOS = 1473180070851117108
CANAL_PAGOS = 1473182832225554554

# 🔴 SEU ID DO DISCORD
MEU_ID = 1439411460378726530
CARGO_ADMIN = 1472666559049633952

carrinhos_ativos = {}

# ===============================
# SISTEMA DE GERENCIAMENTO DE PRODUTOS
# ===============================

def carregar_produtos():
    if os.path.exists(ARQUIVO_PRODUTOS_JSON):
        with open(ARQUIVO_PRODUTOS_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        produtos_padrao = {
            "cs": {
                "nome": "Pack Counter Strike",
                "preco": 24.99,
                "descricao": "✅ Pack completo do Counter Strike\n✅ Acesso vitalício\n✅ Garantia de 30 dias\n✅ Suporte 24/7",
                "tipo": "auto",
                "imagem": ""
            },
            "rockstar": {
                "nome": "Conta Rockstar",
                "preco": 4.99,
                "descricao": "✅ Rockstar nova e nunca utilizada\n✅ Conta Rockstar 100% segura\n✅ Acesso total (Full Acesso)\n✅ Ideal para unban do CFX Global e HWID do FiveM\n✅ Já com licença ativa para jogar FiveM",
                "tipo": "manual",
                "imagem": ""
            }
        }
        salvar_produtos(produtos_padrao)
        return produtos_padrao

def salvar_produtos(produtos):
    with open(ARQUIVO_PRODUTOS_JSON, 'w', encoding='utf-8') as f:
        json.dump(produtos, f, indent=2, ensure_ascii=False)

produtos_disponiveis = carregar_produtos()
print(f"📦 {len(produtos_disponiveis)} produtos carregados")

# ===============================
# MERCADO PAGO
# ===============================
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def criar_pagamento_pix(user_id, produto_id):
    if produto_id not in produtos_disponiveis:
        return None
    
    produto_info = produtos_disponiveis[produto_id]
    
    payment_data = {
        "transaction_amount": produto_info["preco"],
        "description": produto_info["nome"],
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
                "produto": produto_info["nome"],
                "preco": produto_info["preco"],
                "payment_id": payment["id"],
                "produto_id": produto_id,
                "tipo": produto_info.get("tipo", "auto")
            }
    except Exception as e:
        print(f"❌ Erro PIX: {e}")
        return None
    
    return None

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
            "usuario": user.id
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
# COMANDO DE COMPRA
# ===============================
@bot.tree.command(name="comprar", description="Comprar um produto da loja")
@app_commands.describe(produto="ID do produto (use /produtos para ver os IDs)")
async def comprar(interaction: discord.Interaction, produto: str = "cs"):
    await interaction.response.defer(ephemeral=True)
    user = interaction.user
    
    try:
        if produto not in produtos_disponiveis:
            produtos_lista = "\n".join([f"• `{pid}` - {p['nome']}" for pid, p in produtos_disponiveis.items()])
            await interaction.followup.send(
                f"❌ Produto não encontrado!\n\n📦 Produtos disponíveis:\n{produtos_lista}",
                ephemeral=True
            )
            return
        
        pix_data = criar_pagamento_pix(user.id, produto)
        
        if not pix_data:
            await interaction.followup.send("❌ Erro ao gerar pagamento. Tente novamente mais tarde.", ephemeral=True)
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
        await interaction.followup.send("❌ Ocorreu um erro. Contate um administrador.", ephemeral=True)

# ===============================
# COMANDOS DE CLIENTE
# ===============================
@bot.tree.command(name="produtos", description="Ver todos os produtos disponíveis")
async def listar_produtos(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 NOSSOS PRODUTOS",
        description="Use `/comprar [id]` para adquirir qualquer produto!",
        color=0x2b2d31,
        timestamp=datetime.now()
    )
    
    for key, prod in produtos_disponiveis.items():
        tipo_texto = "Automática" if prod.get('tipo') == 'auto' else "Manual"
        
        embed.add_field(
            name=f"📦 {prod['nome']}",
            value=f"💰 Preço: R$ {prod['preco']:.2f}\n"
                  f"📝 Entrega: {tipo_texto}\n"
                  f"🆔 ID: `{key}`",
            inline=False
        )
    
    embed.set_footer(text="Legend Store")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ===============================
# COMANDO /LOJA
# ===============================
@bot.tree.command(name="loja", description="🛒 Ver todos os produtos da loja")
async def mostrar_loja(interaction: discord.Interaction):
    embed = discord.Embed(
        title="LEGEND STORE",
        description="Selecione um produto abaixo",
        color=0x2b2d31,
        timestamp=datetime.now()
    )
    
    for key, prod in produtos_disponiveis.items():
        desc_formatada = prod.get('descricao', 'Sem descrição')
        
        embed.add_field(
            name=f"📦 {prod['nome']}",
            value=f"{desc_formatada}\n\n💰 Preço: R$ {prod['preco']:.2f}\n🆔 ID: `{key}`",
            inline=False
        )
    
    embed.set_footer(text="Legend Store - Clique nos botões abaixo para comprar!")
    
    view = discord.ui.View(timeout=None)
    
    for key, prod in produtos_disponiveis.items():
        button = discord.ui.Button(
            label=f"🛒 {prod['nome']} - R$ {prod['preco']:.2f}",
            style=discord.ButtonStyle.success,
            custom_id=f"loja_comprar_{key}",
            emoji="🛒"
        )
        
        async def button_callback(interaction: discord.Interaction, produto_id=key):
            await interaction.response.defer(ephemeral=True)
            user = interaction.user
            
            try:
                pix_data = criar_pagamento_pix(user.id, produto_id)
                
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
        
        button.callback = button_callback
        view.add_item(button)
    
    await interaction.response.send_message(embed=embed, view=view)

# ===============================
# COMANDOS PARA CANAIS INDIVIDUAIS - VISUAL LIMPO
# ===============================

async def criar_embed_produto(produto_id: str, produto_info: dict):
    """Cria um embed limpo e profissional para o produto (estilo Legend Store)"""
    
    imagem_url = produto_info.get('imagem', '')
    
    embed = discord.Embed(
        title=f"{produto_info['nome'].upper()}",
        description=produto_info.get('descricao', 'Sem descrição'),
        color=0x2b2d31,
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="PREÇO",
        value=f"R$ {produto_info['preco']:.2f}",
        inline=False
    )
    
    embed.set_footer(text="Legend Store - Clique no botão abaixo para comprar!")
    
    if imagem_url and imagem_url != "":
        embed.set_thumbnail(url=imagem_url)
    
    return embed


class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str):
        super().__init__(timeout=None)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
    
    @discord.ui.button(label="COMPRAR AGORA", style=discord.ButtonStyle.success, emoji="🛒")
    async def comprar_agora(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        
        try:
            pix_data = criar_pagamento_pix(user.id, self.produto_id)
            
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
    produto_id="ID do produto (ex: rockstar, cs)",
    nome_canal="Nome do canal (ex: rockstar, cs-vip)"
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
    view = ProdutoCompraView(produto_id, produto_info['nome'])
    
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
            view = ProdutoCompraView(produto_id, produto['nome'])
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
            
            embed = await criar_embed_produto(produto_id, produtos_disponiveis[produto_id])
            view = ProdutoCompraView(produto_id, produtos_disponiveis[produto_id]['nome'])
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
# COMANDOS DE ADMIN
# ===============================

@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
@app_commands.describe(
    id="ID único do produto",
    nome="Nome do produto",
    preco="Preço em R$",
    descricao="Descrição do produto",
    tipo="Tipo: auto ou manual"
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
        "imagem": ""
    }
    salvar_produtos(produtos_disponiveis)
    
    tipo_texto = "🤖 Entrega automática" if tipo == "auto" else "👨‍💼 Entrega manual"
    
    await interaction.response.send_message(
        f"✅ Produto criado!\n\n📦 ID: `{id}`\n📝 Nome: {nome}\n💰 Preço: R$ {preco:.2f}\n🎮 Tipo: {tipo_texto}\n\n💡 Use `/configurar_produto {id} {id}` para criar o canal!",
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


@bot.tree.command(name="entregar", description="[ADMIN] Entregar produto manual")
@app_commands.describe(
    usuario="ID do usuário",
    produto_id="ID do produto",
    conteudo="Conteúdo a entregar"
)
async def entregar_produto(
    interaction: discord.Interaction, 
    usuario: str, 
    produto_id: str,
    conteudo: str
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
        
        produto = produtos_disponiveis[produto_id]
        
        await user.send(
            f"🎮 Sua {produto['nome']} chegou!\n\n"
            f"```{conteudo}```\n\n"
            "✅ Obrigado pela preferência!"
        )
        
        await interaction.followup.send(f"✅ {produto['nome']} entregue para {user.name}!", ephemeral=True)
        
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(
                title="📦 PRODUTO ENTREGUE",
                color=0x3498db,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 Cliente", value=user.mention, inline=True)
            embed.add_field(name="📦 Produto", value=produto['nome'], inline=True)
            embed.add_field(name="🔐 Conteúdo", value=f"||{conteudo}||", inline=False)
            embed.set_footer(text=f"Entregue por: {interaction.user.name}")
            await canal_pagos.send(embed=embed)
        
    except ValueError:
        await interaction.followup.send("❌ ID inválido.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

# ===============================
# BACKUP E RESTAURAR (OPCIONAL)
# ===============================

@bot.tree.command(name="backup", description="[ADMIN] Fazer backup dos produtos")
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

# ===============================
# WEBHOOK
# ===============================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot está online e funcionando!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 Webhook recebido:", data)
    
    try:
        if data and "data" in data and "id" in data["data"]:
            payment_id = data["data"]["id"]
            payment_response = sdk.payment().get(payment_id)
            
            if payment_response["status"] == 200:
                payment = payment_response["response"]
                
                if payment["status"] == "approved":
                    ref = payment.get("external_reference", "")
                    if ref:
                        partes = ref.split('_')
                        if len(partes) >= 2:
                            produto_id = partes[0]
                            user_id = int(partes[1])
                            
                            if user_id == MEU_ID:
                                return "OK", 200
                            
                            user = bot.get_user(user_id)
                            if not user:
                                future = asyncio.run_coroutine_threadsafe(
                                    bot.fetch_user(user_id), bot.loop
                                )
                                user = future.result(timeout=5)
                            
                            if user and produto_id in produtos_disponiveis:
                                produto_info = produtos_disponiveis[produto_id]
                                
                                if produto_info.get("tipo") == "auto":
                                    if produto_id == "cs":
                                        asyncio.run_coroutine_threadsafe(
                                            user.send(
                                                "✅ Pagamento confirmado!\nAqui está seu produto:",
                                                file=discord.File(ARQUIVO_PRODUTO)
                                            ), bot.loop
                                        )
                                    else:
                                        asyncio.run_coroutine_threadsafe(
                                            user.send(
                                                f"✅ Pagamento confirmado!\n\n📦 {produto_info['nome']}\n🔐 Conteúdo será enviado em breve."
                                            ), bot.loop
                                        )
                                else:
                                    asyncio.run_coroutine_threadsafe(
                                        user.send(
                                            f"✅ Pagamento aprovado!\n\n📦 {produto_info['nome']}\n👨‍💼 Um administrador irá entregar seu produto em breve."
                                        ), bot.loop
                                    )
                                
                                asyncio.run_coroutine_threadsafe(
                                    log_pagamento_confirmado(user, produto_info["nome"], produto_info["preco"], payment_id),
                                    bot.loop
                                )
    except Exception as e:
        print(f"❌ Erro webhook: {e}")
    
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

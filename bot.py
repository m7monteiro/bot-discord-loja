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
    """Gera um pagamento PIX com logs detalhados para diagnóstico"""
    try:
        # 1. Validação e formatação do preço (deve ser float com 2 casas)
        preco_formatado = round(float(preco), 2)
        
        # 2. Preparação dos dados (Adicionando campos de identificação que o MP exige em algumas contas)
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

        # Só adiciona notification_url se ela começar com https (exigência do MP)
        if WEBHOOK_URL and WEBHOOK_URL.startswith("https"):
            payment_data["notification_url"] = WEBHOOK_URL

        print(f"🔍 Tentando gerar PIX de R$ {preco_formatado} para o produto {produto_id}...")
        
        # 3. Chamada à API
        result = sdk.payment().create(payment_data)
        
        # 4. Análise do Resultado
        status_code = result.get("status")
        response_data = result.get("response")

        if status_code in [200, 201]:
            payment = response_data
            pix_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
            
            print(f"✅ PIX Gerado com sucesso! ID: {payment.get('id')}")
            return {
                "qr_code": pix_data.get("qr_code"),
                "qr_code_base64": pix_data.get("qr_code_base64"),
                "expiration": payment.get("date_of_expiration"),
                "produto": nome_produto,
                "preco": preco_formatado,
                "payment_id": payment.get("id"),
                "produto_id": produto_id
            }
        else:
            # LOG SUPER DETALHADO PARA O USUÁRIO
            print("\n" + "!"*30)
            print(f"❌ ERRO NA API DO MERCADO PAGO")
            print(f"Status Code: {status_code}")
            print(f"Resposta: {json.dumps(response_data, indent=2)}")
            print("!"*30 + "\n")
            return None

    except Exception as e:
        print(f"❌ ERRO CRÍTICO NO CÓDIGO DE PAGAMENTO: {e}")
        import traceback
        traceback.print_exc()
        return None

# ===============================
# FUNÇÃO PARA ENTREGAR PRODUTO DO ESTOQUE
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
# MODAL PARA 2FA
# ===============================
class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA"):
    chave = discord.ui.TextInput(
        label="Chave 2FA",
        placeholder="Cole sua chave aqui (ex: 7J64V3P3E77J3LKN...)",
        min_length=16,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            chave_limpa = self.chave.value.strip().upper()
            totp = pyotp.TOTP(chave_limpa)
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
            embed.add_field(name="🔑 **SUA CHAVE:**", value=f"||{chave_limpa}||", inline=False)
            embed.set_footer(text="O código expira em 30 segundos.")
            
            # Botão para copiar o código gerado
            class CopiarCodigoView(discord.ui.View):
                def __init__(self, codigo: str):
                    super().__init__(timeout=60)
                    self.codigo = codigo
                @discord.ui.button(label="📋 Copiar Código", style=discord.ButtonStyle.success)
                async def copiar(self, i: discord.Interaction, b: discord.ui.Button):
                    await i.response.send_message(f"{self.codigo}", ephemeral=True)

            await interaction.response.send_message(embed=embed, view=CopiarCodigoView(codigo_atual), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erro ao gerar código: {e}", ephemeral=True)

# ===============================
# VIEW PARA O CANAL 2FA
# ===============================
class Canal2FAView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔐 Gerar Código 2FA", style=discord.ButtonStyle.success, custom_id="btn_gerar_2fa")
    async def gerar_2fa_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(Modal2FA())

# ===============================
# CLASSE DO MENU DE VARIAÇÕES
# ===============================
class VariacoesView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list):
        super().__init__(timeout=300)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes
        
        options = []
        for i, v in enumerate(variacoes):
            options.append(discord.SelectOption(
                label=v["nome"],
                description=f"R$ {v['preco']:.2f}",
                value=str(i)
            ))
        
        select = discord.ui.Select(
            placeholder="Escolha uma opção...",
            options=options,
            custom_id="select_variacao"
        )
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Garante que apenas o usuário que invocou o comando pode interagir
        if interaction.user != self.message.interaction.user:
            await interaction.response.send_message("❌ Esta interação é apenas para quem a iniciou.", ephemeral=True)
            return False
        return True

    @discord.ui.select(custom_id="select_variacao")
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        
        selected_index = int(select.values[0])
        variacao_selecionada = self.variacoes[selected_index]
        
        user = interaction.user
        produto_info = produtos_disponiveis[self.produto_id]
        
        # Verifica estoque da variação
        qtd_estoque_variacao = verificar_estoque(self.produto_id, variacao_selecionada["nome"])
        if qtd_estoque_variacao == 0:
            await interaction.followup.send(f"❌ **Variação '{variacao_selecionada['nome']}' esgotada!** Aguarde reposição.", ephemeral=True)
            return

        pix_data = criar_pagamento_pix_com_preco(
            user.id,
            self.produto_id,
            variacao_selecionada["preco"],
            f"{self.produto_nome} ({variacao_selecionada['nome']})"
        )
        
        if not pix_data:
            await interaction.followup.send("❌ Erro ao gerar pagamento para a variação.", ephemeral=True)
            return
        
        await log_carrinho_ativo(
            user=user,
            produto_nome=pix_data['produto'],
            valor=pix_data['preco'],
            pagamento_id=pix_data.get('payment_id', 'N/A')
        )
        
        embed_pix = discord.Embed(
            title="🧾 PAGAMENTO PIX - Variação",
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
        
        # Envia a imagem do QR Code
        qr_file = discord.File(BytesIO(qr_image_data), filename="qrcode.png")
        embed_pix.set_image(url="attachment://qrcode.png")
        
        await interaction.followup.send(
            embed=embed_pix,
            file=qr_file,
            view=copiar_view,
            ephemeral=True
        )

# ===============================
# COMANDOS DE ADMIN - ESTOQUE
# ===============================
@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    itens="Itens a adicionar (um por linha)",
    variacao="Nome da variação (opcional)"
)
async def add_estoque(
    interaction: discord.Interaction,
    produto_id: str,
    itens: str,
    variacao: str = None
):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    novos_itens = [item.strip() for item in itens.split('\n') if item.strip()]
    if not novos_itens:
        await interaction.followup.send("❌ Nenhum item válido para adicionar.", ephemeral=True)
        return
    
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
        
        if variacao:
            if "variacoes" not in estoque_disponivel[produto_id]:
                estoque_disponivel[produto_id]["variacoes"] = {}
            
            if variacao not in estoque_disponivel[produto_id]["variacoes"]:
                estoque_disponivel[produto_id]["variacoes"][variacao] = []
            
            estoque_disponivel[produto_id]["variacoes"][variacao].extend(novos_itens)
        else:
            estoque_disponivel[produto_id]["itens"].extend(novos_itens)
            
        salvar_estoque(estoque_disponivel)
    
    local = f"na variação `{variacao}`" if variacao else "no estoque geral"
    await interaction.followup.send(f"✅ {len(novos_itens)} itens adicionados {local} para `{produtos_disponiveis[produto_id]['nome']}`!", ephemeral=True)

@bot.tree.command(name="remover_estoque", description="[ADMIN] Remover item do estoque por índice")
@app_commands.describe(
    produto_id="ID do produto",
    indice="Índice do item a remover (use /ver_estoque para ver)",
    variacao="Nome da variação (opcional)"
)
async def remover_estoque(
    interaction: discord.Interaction,
    produto_id: str,
    indice: int,
    variacao: str = None
):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            await interaction.followup.send(f"❌ Estoque para `{produto_id}` não encontrado.", ephemeral=True)
            return
        
        itens_list = []
        if variacao:
            if variacao in estoque_disponivel[produto_id].get("variacoes", {}):
                itens_list = estoque_disponivel[produto_id]["variacoes"][variacao]
            else:
                await interaction.followup.send(f"❌ Variação `{variacao}` não encontrada para o produto `{produto_id}`.", ephemeral=True)
                return
        else:
            itens_list = estoque_disponivel[produto_id].get("itens", [])
        
        if not itens_list:
            await interaction.followup.send(f"❌ Estoque vazio para {produtos_disponiveis[produto_id]['nome']}{f' na variação {variacao}' if variacao else ''}.", ephemeral=True)
            return
        
        if indice < 0 or indice >= len(itens_list):
            await interaction.followup.send(f"❌ Índice inválido! Use 0 a {len(itens_list)-1}.", ephemeral=True)
            return
        
        item_removido = itens_list.pop(indice)
        salvar_estoque(estoque_disponivel)
    
    local = f"da variação `{variacao}`" if variacao else "do estoque geral"
    await interaction.followup.send(f"✅ Item removido {local}: `{item_removido}` para `{produtos_disponiveis[produto_id]['nome']}`!", ephemeral=True)

@bot.tree.command(name="ver_estoque", description="[ADMIN] Ver itens no estoque")
@app_commands.describe(produto_id="ID do produto", variacao="Nome da variação (opcional)")
async def ver_estoque(interaction: discord.Interaction, produto_id: str, variacao: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis[produto_id]
    
    if variacao:
        itens = estoque_disponivel.get(produto_id, {}).get("variacoes", {}).get(variacao, [])
    else:
        itens = estoque_disponivel.get(produto_id, {}).get("itens", [])
    
    if not itens:
        await interaction.followup.send(f"📦 **{produto['nome']}**\n\nEstoque vazio!", ephemeral=True)
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
    
    await interaction.followup.send(embed=embed, ephemeral=True)

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
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    if "variacoes" not in produtos_disponiveis[produto_id]:
        produtos_disponiveis[produto_id]["variacoes"] = []
    
    produtos_disponiveis[produto_id]["variacoes"].append({
        "nome": nome,
        "preco": preco
    })
    salvar_produtos(produtos_disponiveis)
    
    await interaction.followup.send(
        f"✅ Variação adicionada!\n\n"
        f"📦 Produto: {produtos_disponiveis[produto_id]['nome']}\n"
        f"🎮 Opção: {nome}\n"
        f"💰 Preço: R$ {preco:.2f}",
        ephemeral=True
    )

@bot.tree.command(name="listar_variacoes", description="[ADMIN] Listar variações de um produto")
@app_commands.describe(produto_id="ID do produto")
async def listar_variacoes(interaction: discord.Interaction, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis[produto_id]
    variacoes = produto.get("variacoes", [])
    
    if not variacoes:
        await interaction.followup.send(f"📦 **{produto['nome']}**\n\nNenhuma variação cadastrada.\n\nUse `/add_variacao` para criar!", ephemeral=True)
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
    
    await interaction.followup.send(embed=embed, ephemeral=True)

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
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
    if indice < 0 or indice >= len(variacoes):
        await interaction.followup.send(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
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
    
    await interaction.followup.send(mensagem, ephemeral=True)

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
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
        return
    
    variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
    if indice < 0 or indice >= len(variacoes):
        await interaction.followup.send(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
        return
    
    removida = variacoes.pop(indice)
    salvar_produtos(produtos_disponiveis)
    
    await interaction.followup.send(
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
    await interaction.response.defer(ephemeral=True)
    if not produtos_disponiveis:
        await interaction.followup.send("📦 **Nenhum produto cadastrado ainda!**\n\nUse `/criar_produto` para adicionar.", ephemeral=True)
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
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="loja", description="🛒 Ver todos os produtos da loja")
async def mostrar_loja(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not produtos_disponiveis:
        await interaction.followup.send("📦 **Nenhum produto cadastrado ainda!**\n\nUse `/criar_produto` para adicionar.", ephemeral=True)
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
    
    await interaction.followup.send(embed=embed, ephemeral=True)

# ===============================
# COMANDOS PARA CANAIS INDIVIDUAIS
# ===============================

async def criar_embed_produto(produto_id: str, produto_info: dict):
    imagem_url = produto_info.get('imagem', '')
    qtd_variacoes = len(produto_info.get("variacoes", []))
    qtd_estoque = verificar_estoque(produto_id)
    
    # Layout idêntico à imagem de referência
    descricao_base = produto_info.get('descricao', 'Sem descrição')
    
    # Adiciona as linhas de checklist da imagem
    descricao_formatada = f"⚡ **Entrega Automática!**\n\n**{produto_info['nome']}**\n\n"
    
    # Se a descrição original já tiver as linhas, usa ela, senão adiciona um padrão
    if "Conta full acesso" not in descricao_base:
        descricao_formatada += "┃ Conta full acesso.\n┃ Conta verificada.\n┃ Conta sem ban global/hwid.\n\n"
    else:
        descricao_formatada += f"{descricao_base}\n\n"
        
    embed = discord.Embed(
        description=descricao_formatada,
        color=0x2b2d31
    )
    
    # Campo de valor e estoque na mesma linha como na imagem
    valor_estoque = f"💵 **Valor à vista** `R$ {produto_info['preco']:.2f}` | **Restam** `{qtd_estoque}`\nClique no botão **Comprar** ao lado"
    embed.add_field(name="\u200b", value=valor_estoque, inline=False)
    
    embed.set_footer(text="Tzada$tore #2k")
    
    if imagem_url and imagem_url != "":
        embed.set_image(url=imagem_url)
    
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
            
            # Envia a imagem do QR Code
            qr_file = discord.File(BytesIO(qr_image_data), filename="qrcode.png")
            embed_pix.set_image(url="attachment://qrcode.png")
            
            await interaction.followup.send(
                embed=embed_pix,
                file=qr_file,
                view=copiar_view,
                ephemeral=True
            )

        except Exception as e:
            print(f"❌ Erro ao processar compra: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send("❌ Ocorreu um erro ao processar sua compra. Tente novamente mais tarde.", ephemeral=True)


@bot.tree.command(name="configurar_produto", description="[ADMIN] Envia um embed de produto para um canal específico")
@app_commands.describe(
    canal_id="ID do canal onde o embed será enviado",
    produto_id="ID do produto a ser exibido"
)
async def configurar_produto(interaction: discord.Interaction, canal_id: str, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    try:
        canal = bot.get_channel(int(canal_id))
        if not canal:
            await interaction.followup.send(f"❌ Canal com ID `{canal_id}` não encontrado.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send(f"❌ Produto com ID `{produto_id}` não encontrado.", ephemeral=True)
            return
        
        produto_info = produtos_disponiveis[produto_id]
        embed = await criar_embed_produto(produto_id, produto_info)
        
        view = ProdutoCompraView(produto_id, produto_info["nome"], produto_info.get("variacoes", []))
        
        await canal.send(embed=embed, view=view)
        await interaction.followup.send(f"✅ Embed do produto `{produto_info['nome']}` enviado para o canal `{canal.name}`!", ephemeral=True)
        
    except ValueError:
        await interaction.followup.send("❌ ID de canal inválido. Certifique-se de que é um número.", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao configurar produto: {e}")
        await interaction.followup.send(f"❌ Ocorreu um erro ao configurar o produto: {e}", ephemeral=True)

# ===============================
# COMANDOS DE ADMIN - PRODUTOS
# ===============================
@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
@app_commands.describe(
    id="ID único do produto (ex: gta5_conta)",
    nome="Nome do produto (ex: Conta GTA V)",
    preco="Preço base do produto em R$",
    descricao="Descrição do produto",
    tipo="Tipo de entrega (auto para automática, manual para manual)"
)
async def criar_produto(
    interaction: discord.Interaction,
    id: str,
    nome: str,
    preco: float,
    descricao: str,
    tipo: str
):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if id in produtos_disponiveis:
        await interaction.followup.send(f"❌ Já existe um produto com o ID `{id}`!", ephemeral=True)
        return
    
    if tipo not in ["auto", "manual"]:
        await interaction.followup.send("❌ Tipo de entrega inválido. Use 'auto' ou 'manual'.", ephemeral=True)
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
    
    await interaction.followup.send(
        f"✅ Produto criado!\n\n📦 ID: `{id}`\n📝 Nome: {nome}\n💰 Preço: R$ {preco:.2f}\n🎮 Tipo: {tipo_texto}\n\n💡 Use `/add_estoque` para adicionar itens!\n💡 Use `/add_variacao` para adicionar opções!\n💡 Use `/configurar_produto {id} {id}` para criar o canal!",
        ephemeral=True
    )

@bot.tree.command(name="editar_preco", description="[ADMIN] Alterar preço de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    novo_preco="Novo preço em R$"
)
async def editar_preco(interaction: discord.Interaction, produto_id: str, novo_preco: float):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis[produto_id]
    preco_antigo = produto["preco"]
    produto["preco"] = novo_preco
    salvar_produtos(produtos_disponiveis)
    
    await interaction.followup.send(
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
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto não encontrado!", ephemeral=True)
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
    await interaction.followup.send(mensagem, ephemeral=True)

@bot.tree.command(name="remover_produto", description="[ADMIN] Remover um produto")
@app_commands.describe(produto_id="ID do produto")
async def remover_produto(interaction: discord.Interaction, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    produto = produtos_disponiveis.pop(produto_id)
    salvar_produtos(produtos_disponiveis)
    
    await interaction.followup.send(f"✅ Produto removido!\n📦 Removido: {produto['nome']}", ephemeral=True)

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
async def fazer_backup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    backup_data = json.dumps(produtos_disponiveis, indent=2, ensure_ascii=False)
    import io
    file = discord.File(io.StringIO(backup_data), filename="backup_produtos.json")
    
    await interaction.followup.send(
        "✅ Backup realizado! Guarde este arquivo.",
        file=file,
        ephemeral=True
    )

# ===============================
# COMANDO 2FA (ADICIONADO COM SEGURANÇA)
# ===============================
@bot.tree.command(name="2fa", description="Gerar código 2FA a partir da chave")
@app_commands.describe(chave="Sua chave 2FA (ex: 7J64V3P3E77J3LKNUGSZ5QANTLRLTKVL)")
async def gerar_2fa(interaction: discord.Interaction, chave: str):
    await interaction.response.defer(ephemeral=True)
    """Gera o código 2FA atual a partir da chave fornecida"""
    try:
        chave = chave.strip().upper()
        if len(chave) < 16:
            embed = discord.Embed(
                title="❌ **CHAVE INVÁLIDO**",
                description="A chave deve ter pelo menos 16 caracteres.",
                color=0xff0000,
                timestamp=datetime.now()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
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
        embed.add_field(name="🔑 **SUA CHAVE:**", value=f"||{chave}||", inline=False)
        embed.set_footer(text="O código expira em 30 segundos.")
        
        # Botão para copiar o código gerado
        class CopiarCodigoView(discord.ui.View):
            def __init__(self, codigo: str):
                super().__init__(timeout=60)
                self.codigo = codigo
            @discord.ui.button(label="📋 Copiar Código", style=discord.ButtonStyle.success)
            async def copiar(self, i: discord.Interaction, b: discord.ui.Button):
                await i.response.send_message(f"{self.codigo}", ephemeral=True)

        await interaction.followup.send(embed=embed, view=CopiarCodigoView(codigo_atual), ephemeral=True)
    except Exception as e:
        print(f"❌ Erro 2FA: {e}")
        await interaction.followup.send("❌ Erro ao gerar código. Verifique a chave.", ephemeral=True)

# ===============================
# WEBHOOK
# ===============================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 M7 STORE - Bot está online e funcionando!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    start_time = time.time()
    print("\n" + "⚡" * 20)
    print(f"WEBHOOK RECEBIDO ÀS {datetime.now().strftime('%H:%M:%S')}")
    
    # Tenta pegar os dados de várias formas (JSON, Form, Args)
    data = {}
    if request.is_json:
        data = request.json
    elif request.form:
        data = request.form.to_dict()
    
    print(f"📩 Dados recebidos: {json.dumps(data, indent=2)}")
    
    # O Mercado Pago envia o ID de formas diferentes dependendo do tipo de evento
    payment_id = None
    
    # 1. Tenta extrair de data.id (comum em notificações de pagamento)
    if isinstance(data, dict):
        payment_id = data.get('data', {}).get('id')
        
        # 2. Tenta extrair do ID direto (comum em outros eventos)
        if not payment_id:
            payment_id = data.get('id')
            
        # 3. Tenta extrair de resource (alguns webhooks enviam a URL do recurso)
        if not payment_id and 'resource' in data:
            resource = data.get('resource', '')
            payment_id = resource.split('/')[-1] if '/' in resource else None

    # 4. Tenta extrair dos parâmetros da URL (Query Args)
    if not payment_id:
        payment_id = request.args.get('id') or request.args.get('data.id')
    
    if not payment_id:
        print("⚠️ Webhook recebido, mas nenhum ID de pagamento encontrado. Pode ser um teste do MP.")
        return "OK", 200
    
    print(f"💰 ID de Pagamento Identificado: {payment_id}")

    with webhook_lock:
        if str(payment_id) in pagamentos_processados:
            print(f"⚠️ Pagamento {payment_id} já foi processado! Ignorando...")
            return "OK", 200
        
        try:
            print(f"🔍 Buscando pagamento {payment_id} no Mercado Pago...")
            payment_response = sdk.payment().get(payment_id)
            print(f"📦 Resposta do MP: status={payment_response.get('status')}")
            
            if payment_response["status"] == 200:
                payment = payment_response["response"]
                print(f"✅ Status do pagamento: {payment.get('status')}")
                
                if payment["status"] == "approved":
                    print("🎉 PAGAMENTO APROVADO!")
                    
                    pagamentos_processados.add(str(payment_id))
                    salvar_pagamentos_processados(pagamentos_processados)
                    print(f"✅ Pagamento {payment_id} marcado como processado")
                    
                    ref = payment.get("external_reference", "")
                    print(f"🔗 External reference: {ref}")
                    
                    if ref:
                        # Melhoria no parsing: O external_reference é gerado como: {produto_id}_{user_id}_{timestamp}
                        # Ou para variações: {produto_id}_{variacao}_{user_id}_{timestamp}
                        partes = ref.split('_')
                        print(f"🧩 Partes da referência: {partes}")
                        
                        if len(partes) >= 3:
                            # O último é sempre o timestamp, o penúltimo é sempre o user_id
                            user_id = int(partes[-2])
                            timestamp = partes[-1]
                            
                            # O que sobrar no início é o produto e a variação
                            # Se tiver 3 partes: [produto, user_id, timestamp]
                            # Se tiver 4 partes: [produto, variacao, user_id, timestamp]
                            if len(partes) == 3:
                                produto_id = partes[0]
                                variacao_nome = None
                            else:
                                produto_id = partes[0]
                                variacao_nome = partes[1]
                            
                            print(f"📦 Produto ID: {produto_id}")
                            print(f"👤 User ID: {user_id}")
                            
                            # REMOVIDO: Trava de pagamento do próprio dono para permitir testes
                            # if user_id == MEU_ID:
                            #     print("⚠️ Pagamento do próprio dono, ignorando")
                            
                            # Buscar o usuário do Discord
                            user = bot.get_user(user_id)
                            if not user:
                                print(f"❌ Usuário Discord {user_id} não encontrado. Tentando fetch...")
                                try:
                                    user = await bot.fetch_user(user_id)
                                except discord.NotFound:
                                    print(f"❌ Usuário Discord {user_id} não encontrado após fetch.")
                                    return "OK", 200
                                except Exception as e:
                                    print(f"❌ Erro ao buscar usuário Discord {user_id}: {e}")
                                    return "OK", 200

                            produto_info = produtos_disponiveis.get(produto_id)
                            if not produto_info:
                                print(f"❌ Produto {produto_id} não encontrado no sistema.")
                                return "OK", 200

                            item_entregue = entregar_do_estoque(produto_id, variacao_nome)

                            if item_entregue:
                                try:
                                    # Enviar o produto ao usuário no DM
                                    await user.send(
                                        f"🎮 **Sua {produto_info['nome']} chegou!**\n\n"
                                        f"```{item_entregue}```\n\n"
                                        "✅ Obrigado pela preferência!"
                                    )
                                    print(f"✅ Produto {produto_info['nome']} entregue via DM para {user.name}")

                                    # Logar no canal de pagos
                                    await log_pagamento_confirmado(
                                        user=user,
                                        produto_nome=produto_info['nome'],
                                        valor=payment.get('transaction_amount'),
                                        pagamento_id=payment_id
                                    )
                                    print(f"✅ Log de pagamento confirmado para {user.name}")

                                except Exception as e:
                                    print(f"❌ Erro ao enviar DM ou logar pagamento: {e}")
                                    # TODO: Implementar sistema de retry ou notificação manual
                            else:
                                print(f"❌ Falha na entrega do item para {user.name}. Estoque vazio ou erro.")
                                # TODO: Notificar admin sobre estoque vazio
                        else:
                            print(f"❌ External reference em formato inválido: {ref}")
                    else:
                        print("⚠️ External reference não encontrada no pagamento.")
                else:
                    print(f"ℹ️ Pagamento {payment_id} não aprovado. Status: {payment['status']}")
            else:
                print(f"❌ Erro ao buscar pagamento {payment_id} no MP. Status: {payment_response['status']}")

        except Exception as e:
            print(f"❌ ERRO NO PROCESSAMENTO DO WEBHOOK: {e}")
            import traceback
            traceback.print_exc()

    end_time = time.time()
    print(f"⚡ WEBHOOK PROCESSADO EM {round(end_time - start_time, 2)} segundos")
    print("⚡" * 20 + "\n")
    return "OK", 200

# ===============================
# INICIAR BOT E FLASK
# ===============================
def run_flask():
    app.run(host="0.0.0.0", port=os.environ.get("PORT", 5000))

if __name__ == "__main__":
    # Inicia o Flask em uma thread separada
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Inicia o bot do Discord
    bot.run(DISCORD_TOKEN)

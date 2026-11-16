# -*- coding: utf-8 -*-
r"""
Automação da tradução PT-BR do Death Note: Kira Game.
Fica na pasta principal (KiraGameTranslation-master). Mais fácil: use o traduzir.bat.

  python auto.py planilha [--refazer]  cria/atualiza traducao\bin.txt e traducao\scenario.txt
  python auto.py ingles               lê a ROM em inglês e põe a referência (#EN) nas planilhas
  python auto.py aplicar              backup + preenche repetidas + glossário + confere + aplica
  python auto.py repack               gera data\dn1_patched.nds
  python auto.py jogar [--limpo]      abre no melonDS (--limpo apaga o save antes)
  python auto.py tudo [--limpo]       aplicar + repack + jogar
  python auto.py vigiar [--limpo]     fica de olho: salvou a planilha -> aplica, gera e reabre o jogo
  python auto.py previa               gera previa.html mostrando cada frase com a fonte do jogo
  python auto.py imagens [--base-jp]  copia pra pasta imagens\ as imagens com texto e abre a galeria
  python auto.py galeria              só abre/atualiza a galeria (japonês x inglês x sua versão)
  python auto.py modo ingles          o que não foi traduzido usa o patch em inglês (pra testar mods já)
  python auto.py modo portugues       volta a usar só o que você traduziu
  python auto.py catalogo             página com TODAS as imagens do jogo, com filtro por nome
  python auto.py mod NOME             copia as imagens com NOME no caminho pra pasta mods\ pra editar
  python auto.py paletas              acha imagens com cores erradas e testa a correção (abre paletas.html)
  python auto.py reextrair            reextrai as imagens usando as paletas corrigidas
  python auto.py replicar IMG PADRAO  aplica as trocas de cor da IMG (em mods) nas imagens com PADRAO no nome
  python auto.py lancar               cria o patch .xdelta numerado na pasta lancamento
  python auto.py status               mostra o progresso
"""
import base64
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib

# KIRA_BASE: pasta do projeto (o painel em .exe define isso, porque o código fica dentro do executável)
BASE = os.environ.get("KIRA_BASE") or os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PASTA = os.path.join(BASE, "traducao")
BACKUPS = os.path.join(BASE, "backups")
LANCAMENTO = os.path.join(BASE, "lancamento")
CONFIG = os.path.join(BASE, "auto_config.json")
GLOSSARIO = os.path.join(BASE, "glossario.txt")
EN_REF = os.path.join(DATA, "en_ref.json")
ROM = os.path.join(DATA, "dn1_patched.nds")
PATCH = os.path.join(DATA, "patch.xdelta")
FONTES = [os.path.join(DATA, "replace", "data", "data", "font", "font.nftr"),
          os.path.join(DATA, "extract", "data", "data", "font", "font.nftr")]
MAX_BACKUPS = 30
IMAGENS = os.path.join(BASE, "imagens")
MODS = os.path.join(BASE, "mods")  # vira a pasta da skin atual (ver atualizar_mods)
SKINS = os.path.join(BASE, "skins")
EN_IMAGENS = os.path.join(DATA, "en_imagens.json")
MANIFESTO = os.path.join(IMAGENS, "manifesto.json")
# tipo: (pasta original JP, pasta EN, pasta que a ferramenta reempacota)
TIPOS_IMG = {"IMG": ("out_IMG", "en_IMG", "work_IMG"), "NSBMD": ("out_NSBMD", "en_NSBMD", "work_NSBMD")}

ARQUIVOS = {
    # nome da planilha: (arquivo extraído, arquivo que a ferramenta lê)
    "bin": ("bin_output.txt", "bin_input.txt"),
    "scenario": ("scenario_output.txt", "scenario_input.txt"),
}

# Precisa ser igual ao ACENTOS do game.py
ACENTOS = {'á': 'а', 'à': 'б', 'â': 'в', 'ã': 'г', 'é': 'д', 'ê': 'е', 'í': 'ё', 'ó': 'ж', 'ô': 'з',
           'õ': 'и', 'ú': 'й', 'ü': 'к', 'ç': 'л', 'Á': 'А', 'À': 'Б', 'Â': 'В', 'Ã': 'Г', 'É': 'Д',
           'Ê': 'Е', 'Í': 'Ё', 'Ó': 'Ж', 'Ô': 'З', 'Õ': 'И', 'Ú': 'Й', 'Ç': 'К'}
TROCAS = {"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "ñ": "n", "è": "e",
          "ì": "i", "ò": "o", "ù": "u", "ï": "i", "ö": "o", "ë": "e", "\t": " "}

JAPONES = re.compile(r"[぀-ヿ一-鿿]")
CODIGOS = re.compile(r"<col\d+>|<[0-9a-fA-F]{2}>|%[0-9]*[a-zA-Z]|UNK\([0-9a-fA-F]+\)")
ITEM = re.compile(r"^@(\d+) (.*)$")
LARGURA_TELA = 240   # linhas centralizadas e telas cheias
LARGURA_FALA = 180   # caixa de diálogo do scenario


# ================================================================ arquivos
def ler(caminho):
    with open(caminho, "rb") as f:
        bruto = f.read()
    nl = "\r\n" if b"\r\n" in bruto else "\n"
    return bruto.decode("utf-8-sig").split(nl), nl


def gravar(caminho, linhas, nl="\n"):
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(linhas))


def separar(linha):
    if linha.startswith("!FILE:") or "=" not in linha:
        return None, None
    chave, valor = linha.split("=", 1)
    return (chave, valor) if chave else (None, None)


def eh_texto(chave):
    return bool(JAPONES.search(chave)) and "UNK(" not in chave and "font/" not in chave


def garantir_input(nome):
    saida, entrada = ARQUIVOS[nome]
    p_in, p_out = os.path.join(DATA, entrada), os.path.join(DATA, saida)
    if not os.path.isfile(p_in):
        if not os.path.isfile(p_out):
            return None
        shutil.copyfile(p_out, p_in)
        print("  criado", entrada, "a partir de", saida)
    return p_in


def ler_planilha(nome):
    """Retorna (linhas, nl, itens) onde itens = [(indice_na_planilha, num, chave, trad)]."""
    p = os.path.join(PASTA, nome + ".txt")
    if not os.path.isfile(p):
        return None, None, []
    linhas, nl = ler(p)
    itens = []
    for i, linha in enumerate(linhas):
        m = ITEM.match(linha)
        if m:
            chave, trad = separar(m.group(2))
            if chave is not None:
                itens.append((i, int(m.group(1)), chave, trad))
    return linhas, nl, itens


# ================================================================ referência em inglês
def carregar_ingles():
    if not os.path.isfile(EN_REF):
        return None
    with open(EN_REF, encoding="utf-8") as f:
        return json.load(f)


def ingles_por_linha(nome, linhas_input, ref):
    """Mapeia número da linha do *_input.txt -> texto em inglês."""
    out = {}
    if ref is None:
        return out
    if nome == "bin":
        for i, linha in enumerate(linhas_input, 1):
            chave, _ = separar(linha)
            if chave in ref["bin"]:
                out[i] = ref["bin"][chave]
        return out
    fila = []
    for i, linha in enumerate(linhas_input, 1):
        if linha.startswith("!FILE:"):
            fila = list(ref["scenario"].get(linha[6:], []))
            continue
        chave, _ = separar(linha)
        if chave is None:
            continue
        for k, par in enumerate(fila):  # próxima ocorrência dessa frase nesse arquivo
            if par[0] == chave:
                if par[1]:
                    out[i] = par[1]
                del fila[k]
                break
    return out


def ingles():
    cfg = config(perguntar_melon=False)
    rom = cfg.get("rom_ingles", "")
    if not os.path.isfile(rom):
        print("Onde está a ROM com o patch em INGLÊS? (arraste o arquivo pra cá e aperte Enter)")
        rom = input("> ").strip().strip('"')
        cfg["rom_ingles"] = rom
        salvar_config(cfg)
    if not rodar_pipenv(["auto_ingles.py", rom]):
        print("  [ERRO] não consegui ler a ROM em inglês (veja as mensagens acima)")
        return
    planilha(refazer=True)


# ================================================================ planilha
def planilha(refazer=False):
    os.makedirs(PASTA, exist_ok=True)
    ref = carregar_ingles()
    for nome in ARQUIVOS:
        p_in = garantir_input(nome)
        if p_in is None:
            print("  [pulei]", nome, "- arquivo não encontrado (rode o extract antes)")
            continue
        destino = os.path.join(PASTA, nome + ".txt")
        if os.path.isfile(destino) and not refazer:
            print("  [já existe]", destino, "(use 'planilha --refazer' pra recriar)")
            continue
        antigas = {}
        if os.path.isfile(destino):
            _, _, itens = ler_planilha(nome)
            antigas = {num: trad for _, num, _, trad in itens}
        linhas, _ = ler(p_in)
        en = ingles_por_linha(nome, linhas, ref)
        out = ["# Planilha de tradução: " + nome,
               "# Escreva depois do '='. Não mexa no '@número' nem no japonês.",
               "# '|' quebra linha, '<<' no começo centraliza, '!' apaga a frase. Não use '#'.",
               "# Linhas '#EN:' são só referência do patch em inglês.", ""]
        n = 0
        for i, linha in enumerate(linhas, 1):
            if linha.startswith("!FILE:"):
                out += ["", "# ===== " + linha[6:] + " ====="]
                continue
            chave, valor = separar(linha)
            if chave is None or not eh_texto(chave):
                continue
            if i in en:
                out.append("#EN: " + en[i].replace("|", " / "))
            trad = antigas.get(i, "") or valor
            out.append("@%d %s=%s" % (i, chave, trad))
            n += 1
        gravar(destino, out)
        print("  [ok]", os.path.relpath(destino, BASE), "-", n, "frases",
              "(com inglês: %d)" % len(en) if en else "")


# ================================================================ glossário
GLOSSARIO_INICIAL = """# Glossário: japonês=português (um por linha). Linhas vazias depois do '=' são ignoradas.
# O 'aplicar' avisa quando uma frase tem o termo japonês mas a tradução não usa o termo escolhido.
# Nomes de personagens
夜神月=Light
竜崎=Ryuzaki
弥海砂=Misa Amane
海砂=Misa
夜神総一郎=Soichiro Yagami
総一郎=Soichiro
松田=Matsuda
相沢=Aizawa
模木=Mogi
ワタリ=Watari
リューク=Ryuk
レム=Rem
メロ=Mello
ニア=Near
火口=Higuchi
高田清美=Kiyomi Takada
魅上照=Teru Mikami
南空ナオミ=Naomi Misora
粧裕=Sayu
# Termos do jogo (preencha do seu jeito)
疑惑度=
信頼度=
発言力=
崇拝者=
同調=
反論=
対抗=
必殺=
検挙=
裁き=
"""


def carregar_glossario():
    if not os.path.isfile(GLOSSARIO):
        with open(GLOSSARIO, "w", encoding="utf-8") as f:
            f.write(GLOSSARIO_INICIAL)
        print("  criado glossario.txt (edite os termos do jogo)")
    termos = []
    linhas, _ = ler(GLOSSARIO)
    for linha in linhas:
        if linha.startswith("#"):
            continue
        jp, pt = separar(linha)
        if jp and pt and pt.strip():
            termos.append((jp.strip(), pt.strip()))
    termos.sort(key=lambda t: -len(t[0]))  # termos maiores primeiro
    return termos


def checar_glossario(chave, trad, termos):
    avisos, usados = [], set()
    for jp, pt in termos:
        if jp in chave and not any(jp in u for u in usados):
            usados.add(jp)
            if pt.lower() not in trad.lower():
                avisos.append("glossário: '%s' deveria virar '%s'" % (jp, pt))
    return avisos


# ================================================================ validação
_FONTE = []


def largura(txt):
    """Largura em pixels usando a fonte do jogo (letras com larguras diferentes)."""
    if not _FONTE:
        caminho = next((f for f in FONTES if os.path.isfile(f)), None)
        _FONTE.append(Fonte(caminho) if caminho else None)
    if _FONTE[0] is not None:
        return sum(_FONTE[0].avanco(c) for c in txt)
    return sum(6 if (ord(c) < 128 or c in ACENTOS) else 12 for c in txt)


def validar(chave, trad, nome="bin"):
    erros, avisos = [], []
    if trad in ("", "!"):
        return trad, erros, avisos
    for a, b in TROCAS.items():
        trad = trad.replace(a, b)
    if "#" in trad:
        erros.append("tem '#', que a ferramenta trata como comentário")
    if sorted(CODIGOS.findall(chave)) != sorted(CODIGOS.findall(trad)):
        erros.append("códigos diferentes do original: precisa ter %s" % (CODIGOS.findall(chave) or "nenhum"))
    elif CODIGOS.findall(chave) != CODIGOS.findall(trad):
        avisos.append("códigos em ordem diferente do original (%s)" % CODIGOS.findall(chave))
    ruins = []
    for c in trad:
        try:
            ACENTOS.get(c, c).encode("shift_jis")
        except UnicodeEncodeError:
            ruins.append(c)
    if ruins:
        erros.append("caracteres que o jogo não tem: " + " ".join(sorted(set(ruins))))
    # o scenario quebra linha sozinho; só confere linhas com '|' manual (ou do bin)
    if not trad.startswith("<<") and (nome == "bin" or "|" in trad):
        for parte in CODIGOS.sub("", trad).split("|"):
            if largura(parte) > LARGURA_TELA:
                avisos.append("linha longa (%dpx, a tela tem %d): '%s'" % (largura(parte), LARGURA_TELA, parte))
    return trad, erros, avisos


# ================================================================ backup
def backup():
    carimbo = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = os.path.join(BACKUPS, carimbo)
    os.makedirs(destino, exist_ok=True)
    for nome in ARQUIVOS:
        plan = os.path.join(PASTA, nome + ".txt")
        inp = os.path.join(DATA, ARQUIVOS[nome][1])
        if os.path.isfile(plan):
            shutil.copy2(plan, os.path.join(destino, "planilha_" + nome + ".txt"))
        if os.path.isfile(inp):
            shutil.copy2(inp, destino)
    if os.path.isfile(GLOSSARIO):
        shutil.copy2(GLOSSARIO, destino)
    pastas = sorted(d for d in os.listdir(BACKUPS)
                    if d[:1].isdigit() and os.path.isdir(os.path.join(BACKUPS, d)))
    for velha in pastas[:-MAX_BACKUPS]:
        shutil.rmtree(os.path.join(BACKUPS, velha), ignore_errors=True)


# ---------------------------------------------------------------- backup diário
DIARIO = os.path.join(BACKUPS, "diario")
MAX_DIARIOS = 14


def ultimo_backup_diario():
    """Nome (AAAA-MM-DD) do backup diário mais recente, ou ""."""
    if not os.path.isdir(DIARIO):
        return ""
    zips = sorted(f for f in os.listdir(DIARIO) if f.endswith(".zip") and f[:1].isdigit())
    return zips[-1][:10] if zips else ""


def backup_diario(forcar=False):
    """Um .zip por dia com traduções, skins, imagens com texto, glossário e configurações.
    Devolve o caminho do zip criado, ou "" se o de hoje já existia ou não havia o que guardar."""
    import zipfile
    hoje = datetime.date.today().strftime("%Y-%m-%d")
    destino = os.path.join(DIARIO, hoje + ".zip")
    if os.path.isfile(destino) and not forcar:
        return ""
    itens = []
    for pasta in (PASTA, SKINS, IMAGENS):
        for raiz, _, arquivos in os.walk(pasta):
            for a in arquivos:
                itens.append(os.path.join(raiz, a))
    for solto in [GLOSSARIO, CONFIG] + [os.path.join(DATA, ARQUIVOS[n][1]) for n in ARQUIVOS]:
        if os.path.isfile(solto):
            itens.append(solto)
    if not itens:
        return ""
    os.makedirs(DIARIO, exist_ok=True)
    temp = destino + ".tmp"
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        for arq in itens:
            try:
                z.write(arq, os.path.relpath(arq, BASE))
            except OSError:
                pass  # arquivo aberto em outro programa: fica pro próximo backup
    os.replace(temp, destino)
    zips = sorted(f for f in os.listdir(DIARIO) if f.endswith(".zip") and f[:1].isdigit())
    for velho in zips[:-MAX_DIARIOS]:
        try:
            os.remove(os.path.join(DIARIO, velho))
        except OSError:
            pass
    return destino


# ================================================================ aplicar
def preencher_repetidas(nome):
    linhas, nl, itens = ler_planilha(nome)
    if linhas is None:
        return 0
    feitas = {}
    for _, _, chave, trad in itens:
        if trad and chave not in feitas:
            feitas[chave] = trad
    n = 0
    for idx, num, chave, trad in itens:
        if not trad and chave in feitas:
            linhas[idx] = "@%d %s=%s" % (num, chave, feitas[chave])
            n += 1
    if n:
        gravar(os.path.join(PASTA, nome + ".txt"), linhas, nl)
    return n


def modo_ingles():
    return config(perguntar_melon=False).get("base_ingles", False)


def aplicar_um(nome, termos):
    p_in = garantir_input(nome)
    _, _, itens = ler_planilha(nome)
    if p_in is None or not itens:
        return 0, 0
    linhas, nl = ler(p_in)
    en = ingles_por_linha(nome, linhas, carregar_ingles()) if modo_ingles() else {}
    mudou = n_erros = n_en = 0
    for idx, num, chave, trad in itens:
        ref = "%s.txt linha %d" % (nome, idx + 1)
        if num < 1 or num > len(linhas) or separar(linhas[num - 1])[0] != chave:
            print("  [ERRO] %s: o japonês não bate com a linha %d do %s" % (ref, num, os.path.basename(p_in)))
            n_erros += 1
            continue
        trad, erros, avisos = validar(chave, trad, nome)
        if trad and trad != "!":
            avisos += checar_glossario(chave, trad, termos)
        for e in erros:
            print("  [ERRO] %s: %s" % (ref, e))
        for a in avisos:
            print("  [aviso] %s: %s" % (ref, a))
        if erros:
            n_erros += 1
            continue
        if not trad and num in en:  # modo inglês: frase sem tradução usa o texto do patch EN
            t_en, e_en, _ = validar(chave, en[num], nome)
            if not e_en:
                # "{{" = já vem quebrado em linhas pelo AGT, a ferramenta não deve quebrar de novo
                trad = ("{{" + t_en) if nome == "scenario" else t_en
                n_en += 1
        if separar(linhas[num - 1])[1] != trad:
            linhas[num - 1] = chave + "=" + trad
            mudou += 1
    gravar(p_in, linhas, nl)
    if n_en:
        print("  [%s] %d frases ainda sem tradução usando o inglês" % (nome, n_en))
    return mudou, n_erros


def aplicar():
    if not os.path.isdir(PASTA):
        print("  Ainda não há planilhas. Rode 'planilha' primeiro.")
        return 0
    backup()
    termos = carregar_glossario()
    total = 0
    for nome in ARQUIVOS:
        rep = preencher_repetidas(nome)
        mudou, erros = aplicar_um(nome, termos)
        total += erros
        extra = ", %d repetidas preenchidas" % rep if rep else ""
        print("  [%s] %d linhas atualizadas, %d com erro (puladas)%s" % (nome, mudou, erros, extra))
    sincronizar_imagens()
    return total


# ================================================================ status
def contar(nome):
    """Conta pelas planilhas (no modo inglês o *_input.txt também tem frases em inglês)."""
    _, _, itens = ler_planilha(nome)
    if itens:
        return sum(1 for i in itens if i[3].strip()), len(itens)
    p_in = os.path.join(DATA, ARQUIVOS[nome][1])
    if not os.path.isfile(p_in):
        return None
    linhas, _ = ler(p_in)
    tot = feitas = 0
    for linha in linhas:
        chave, valor = separar(linha)
        if chave and eh_texto(chave):
            tot += 1
            feitas += valor.strip() != ""
    return feitas, tot


def status():
    for nome in ARQUIVOS:
        garantir_input(nome)
        r = contar(nome)
        if r:
            feitas, tot = r
            print("  %-9s %5d / %5d frases (%.1f%%)" % (nome, feitas, tot, 100.0 * feitas / tot if tot else 0))
    m = carregar_manifesto()
    if m:
        feitas = sum(1 for i in m.values() if editada(i["tipo"], i["rel"], i))
        print("  %-9s %5d / %5d imagens" % ("imagens", feitas, len(m)))
    print("  modo: %s" % ("inglês + suas mudanças" if modo_ingles() else "só português"))
    for sk in listar_skins():
        print("  skin %-22s %4d imagens %s%s" % (sk["nome"], sk["imagens"], "[ligada]" if sk["ativa"] else "",
                                                  " [atual]" if sk["atual"] else ""))


# ================================================================ pipenv / repack / jogar
def comandos_python():
    """No .exe, o próprio executável roda os scripts (já traz o Python e o hacktools)."""
    if getattr(sys, "frozen", False):
        return [[sys.executable, "--rodar"]]
    return [["pipenv", "run", "python"], [sys.executable, "-m", "pipenv", "run", "python"]]


def rodar_pipenv(args):
    for pre in comandos_python():
        try:
            return subprocess.run(pre + args, cwd=BASE).returncode == 0
        except FileNotFoundError:
            continue
    print("  [ERRO] pipenv não encontrado. Rode o run_windows.bat uma vez antes.")
    return False


def gerar_centering():
    """Recria o data/centering.asm do AGT Team (não é público) comparando o código do jogo
    japonês com o do patch em inglês: são as posições X (mov r2,#...) dos textos centralizados."""
    destino = os.path.join(DATA, "centering.asm")
    if os.path.isfile(destino):
        with open(destino, encoding="utf-8") as f:
            if "auto.py" not in f.readline():
                return  # arquivo feito por outra pessoa: não mexo
    jp_p = os.path.join(DATA, "extract", "arm9.bin")
    en_p = os.path.join(DATA, "en_extract", "arm9.bin")
    linhas = ["; gerado pelo auto.py a partir do patch em inglês (posições dos textos centralizados)"]
    if os.path.isfile(jp_p) and os.path.isfile(en_p):
        jp = open(jp_p, "rb").read()
        en = open(en_p, "rb").read()
        texto = [(0x8753c, 0xa3c87), (0x869d0, 0x86b53), (0x87b6c, 0x8811a), (0x882dc, 0x8867f), (0x8821c, 0x882dc)]
        for w in range(0, min(len(jp), len(en)) - 3, 4):
            if any(a <= w <= b for a, b in texto):
                continue
            vj = struct.unpack_from("<I", jp, w)[0]
            ve = struct.unpack_from("<I", en, w)[0]
            if vj != ve and (vj & 0xFFFFF000) == 0xE3A02000 and (ve & 0xFFFFF000) == 0xE3A02000:
                linhas += ["  .org 0x%08x" % (0x02000000 + w), "    .dw 0x%08x" % ve]
    with open(destino, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")


def repack():
    gerar_centering()
    if os.path.isdir(os.path.join(DATA, "work_IMG")):
        rodar_pipenv(["auto_cores.py", "paletas"])  # cores novas nos mods -> paleta
    if not os.path.isfile(FONTES[0]):
        print("  [aviso] fonte com acentos não encontrada em", FONTES[0])
    antes = os.path.getmtime(ROM) if os.path.isfile(ROM) else 0
    if rodar_pipenv(["tool.py", "repack"]) and os.path.isfile(ROM) and os.path.getmtime(ROM) > antes:
        print("  [ok] ROM gerada:", ROM)
        return True
    print("  [ERRO] o repack falhou (veja as mensagens acima)")
    return False


def salvar_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def config(perguntar_melon=True):
    cfg = {}
    if os.path.isfile(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    if perguntar_melon and not os.path.isfile(cfg.get("melonds", "")):
        print("Onde está o melonDS.exe? (arraste o arquivo pra cá e aperte Enter)")
        cfg["melonds"] = input("> ").strip().strip('"')
        salvar_config(cfg)
    return cfg


def apagar_save():
    for ext in (".sav", ".dsv"):
        p = ROM[:-4] + ext
        if os.path.isfile(p):
            os.remove(p)
            print("  save apagado:", os.path.basename(p))


def jogar(limpo=False):
    if not os.path.isfile(ROM):
        print("  [ERRO] ROM não encontrada. Rode o repack primeiro.")
        return None
    if limpo:
        apagar_save()
    proc = subprocess.Popen([config()["melonds"], ROM])
    print("  [ok] abrindo o jogo...")
    return proc


# ================================================================ vigiar
def vigiar(limpo=False):
    alvos = [os.path.join(PASTA, n + ".txt") for n in ARQUIVOS] + [GLOSSARIO]

    def marcas():
        ms = [os.path.getmtime(p) if os.path.isfile(p) else 0 for p in alvos]
        for raiz, _, arqs in os.walk(IMAGENS):
            ms += [os.path.getmtime(os.path.join(raiz, a)) for a in arqs if a.endswith(".png")]
        return ms
    config()
    print("Vigiando as planilhas. Salve um arquivo pra atualizar o jogo. Ctrl+C pra parar.")
    aplicar()
    proc = jogar(limpo) if repack() else None
    ultimo = marcas()
    try:
        while True:
            time.sleep(1.5)
            if marcas() == ultimo:
                continue
            time.sleep(0.5)  # espera o editor terminar de salvar
            print("\n--- mudança detectada", datetime.datetime.now().strftime("%H:%M:%S"), "---")
            aplicar()
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if repack():
                proc = jogar(limpo)
            ultimo = marcas()  # depois do aplicar (que pode preencher repetidas)
    except KeyboardInterrupt:
        print("\nParei de vigiar.")


# ================================================================ prévia com a fonte do jogo
class Fonte:
    def __init__(self, caminho):
        d = open(caminho, "rb").read()
        self.d = d
        plgc, hdwc, pamc = struct.unpack_from("<III", d, 32)
        self.w, self.h = d[plgc], d[plgc + 1]
        self.glen = struct.unpack_from("<H", d, plgc + 2)[0]
        self.base = plgc + 8
        self.hdwc = hdwc + 8
        self.cmap = {}
        o = pamc
        while o:
            ini, fim, tipo, prox = struct.unpack_from("<HHII", d, o)
            p = o + 12
            if tipo == 0:
                t0 = struct.unpack_from("<H", d, p)[0]
                for i in range(fim - ini + 1):
                    self.cmap[ini + i] = t0 + i
            elif tipo == 1:
                for i in range(fim - ini + 1):
                    t = struct.unpack_from("<H", d, p + 2 * i)[0]
                    if t != 0xFFFF:
                        self.cmap[ini + i] = t
            elif tipo == 2:
                n = struct.unpack_from("<H", d, p)[0]
                for i in range(n):
                    c, t = struct.unpack_from("<HH", d, p + 2 + 4 * i)
                    self.cmap[c] = t
            o = prox

    def tile(self, ch):
        ch = ACENTOS.get(ch, ch)
        try:
            b = ch.encode("shift_jis")
        except UnicodeEncodeError:
            return None
        return self.cmap.get(b[0] if len(b) == 1 else (b[0] << 8 | b[1]))

    def metrica(self, t):
        return struct.unpack_from("bBB", self.d, self.hdwc + t * 3)

    def avanco(self, ch):
        t = self.tile(ch)
        return self.metrica(t)[2] if t is not None else 12

    def pixels(self, t):
        o = self.base + t * self.glen
        bits = []
        for b in self.d[o:o + self.glen]:
            bits += [(b >> (7 - i)) & 1 for i in range(8)]
        return bits


def quebrar(fonte, txt, limite):
    linhas = []
    for bloco in txt.split("|"):
        atual, larg = "", 0
        for palavra in bloco.split(" "):
            w = sum(fonte.avanco(c) for c in palavra)
            esp = fonte.avanco(" ") if atual else 0
            if atual and larg + esp + w > limite:
                linhas.append(atual)
                atual, larg = palavra, w
            else:
                atual = (atual + " " + palavra) if atual else palavra
                larg += esp + w
        linhas.append(atual)
    return linhas


def png(largura_px, altura_px, ligados, escala=2):
    W, H = largura_px * escala, altura_px * escala
    linhas = bytearray()
    for y in range(H):
        linhas.append(0)
        for x in range(W):
            v = 255 if (x // escala, y // escala) in ligados else 24
            linhas += bytes((v, v, v))

    def bloco(tipo, dados):
        c = struct.pack(">I", len(dados)) + tipo + dados
        return c + struct.pack(">I", zlib.crc32(tipo + dados) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(bytes(linhas))) + bloco(b"IEND", b""))


def desenhar(fonte, linhas, largura_caixa):
    alt = len(linhas) * (fonte.h + 2) + 2
    ligados = set()
    for n, linha in enumerate(linhas):
        centro = linha.startswith("<<")
        linha = linha[2:] if centro else linha
        total = sum(fonte.avanco(c) for c in linha)
        x = 2 + (max(0, largura_caixa - total) // 2 if centro else 0)
        y0 = 2 + n * (fonte.h + 2)
        for c in linha:
            t = fonte.tile(c)
            if t is None:
                x += 12
                continue
            ini, _, av = fonte.metrica(t)
            bits = fonte.pixels(t)
            for yy in range(fonte.h):
                for xx in range(fonte.w):
                    if bits[yy * fonte.w + xx]:
                        ligados.add((x + ini + xx, y0 + yy))
            x += av
    return png(largura_caixa + 4, alt, ligados)


def previa():
    caminho = next((f for f in FONTES if os.path.isfile(f)), None)
    if caminho is None:
        print("  [ERRO] fonte do jogo não encontrada")
        return
    fonte = Fonte(caminho)
    partes = ["<html><head><meta charset='utf-8'><title>Prévia</title><style>"
              "body{background:#111;color:#ddd;font-family:sans-serif}"
              ".f{margin:10px 0;padding:8px;border-left:4px solid #3a3}.r{border-color:#d33}"
              "img{image-rendering:pixelated;border:1px solid #444;display:block;margin-top:4px}"
              "small{color:#888}</style></head><body><h2>Prévia com a fonte do jogo</h2>"
              "<p>Vermelho = passou da largura ou tem mais de 3 linhas. As quebras automáticas são uma "
              "simulação aproximada da ferramenta.</p>"]
    total = ruins = 0
    for nome in ARQUIVOS:
        _, _, itens = ler_planilha(nome)
        for idx, num, chave, trad in itens:
            if not trad or trad == "!":
                continue
            limpo = CODIGOS.sub("", trad)
            ja_formatado = limpo.startswith("{{")
            if ja_formatado:
                limpo = limpo[2:]
            centro = limpo.startswith("<<")
            if centro:
                limpo = limpo[2:]
            limite = LARGURA_TELA if (centro or nome == "bin") else LARGURA_FALA
            if not ja_formatado and (centro or (nome == "scenario" and "|" not in limpo)):
                linhas = quebrar(fonte, limpo, limite)
            else:
                linhas = limpo.split("|")
            maior = max(sum(fonte.avanco(c) for c in l) for l in linhas)
            ruim = maior > limite or (nome == "scenario" and len(linhas) > 3)
            if centro:
                linhas = ["<<" + l for l in linhas]
            img = base64.b64encode(desenhar(fonte, linhas, limite)).decode()
            partes.append("<div class='f%s'><small>%s.txt linha %d &middot; %dpx de %d &middot; %d linha(s)</small>"
                          "<br>%s<img src='data:image/png;base64,%s'></div>"
                          % (" r" if ruim else "", nome, idx + 1, maior, limite, len(linhas),
                             chave.replace("<", "&lt;"), img))
            total += 1
            ruins += ruim
    partes.append("</body></html>")
    destino = os.path.join(BASE, "previa.html")
    gravar(destino, partes)
    print("  [ok] previa.html: %d frases, %d marcadas em vermelho" % (total, ruins))
    try:
        os.startfile(destino)
    except AttributeError:
        pass


# ================================================================ imagens
def md5(caminho):
    import hashlib
    with open(caminho, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def carregar_manifesto():
    if os.path.isfile(MANIFESTO):
        with open(MANIFESTO, encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_manifesto(m):
    os.makedirs(IMAGENS, exist_ok=True)
    with open(MANIFESTO, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, ensure_ascii=False)


def editada(tipo, rel, info):
    p = os.path.join(IMAGENS, tipo, rel)
    return os.path.isfile(p) and md5(p) != info["base_md5"]


def imagens(base=None):
    if not os.path.isfile(EN_IMAGENS):
        print("  Ainda não sei quais imagens têm texto. Vou comparar com a ROM em inglês...")
        cfg = config(perguntar_melon=False)
        if not os.path.isdir(os.path.join(DATA, "en_extract")) or not os.path.isfile(cfg.get("rom_ingles", "")):
            print("  Rode primeiro a opção 'ingles' (puxar referência em inglês).")
            return
        if not rodar_pipenv(["auto_ingles.py", cfg["rom_ingles"], "--so-imagens"]) or not os.path.isfile(EN_IMAGENS):
            print("  [ERRO] não consegui comparar as imagens (veja as mensagens acima)")
            return
    with open(EN_IMAGENS, encoding="utf-8") as f:
        lista = json.load(f)
    cfg = config(perguntar_melon=False)
    base = base or cfg.get("base_imagem", "en")
    m = carregar_manifesto()
    novas = 0
    for tipo, (jp, en, _) in TIPOS_IMG.items():
        # texturas 3D sempre partem do japonês (a paleta do modelo é a original)
        b = "jp" if tipo == "NSBMD" else base
        for rel in lista.get(tipo, []):
            chave = tipo + "/" + rel
            destino = os.path.join(IMAGENS, tipo, rel)
            if chave in m and os.path.isfile(destino):
                info = m[chave]
                if info["base"] == b or editada(tipo, rel, info):
                    continue  # já preparada (ou já editada: não mexo)
            origem = os.path.join(DATA, en if b == "en" else jp, rel)
            real = b
            if not os.path.isfile(origem):
                origem, real = os.path.join(DATA, jp, rel), "jp"
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(origem, destino)
            m[chave] = {"tipo": tipo, "rel": rel, "base": real, "base_md5": md5(destino)}
            novas += 1
    salvar_manifesto(m)
    if novas:
        print("  [ok] %d imagens copiadas pra pasta imagens\\ (base: %s)" % (novas, "inglês" if base == "en" else "japonês"))
    galeria(m)


EXTRACT_XAP = os.path.join(DATA, "extract_XAP")
EN_XAP = os.path.join(DATA, "en_extract_XAP")
BASE_XAP = os.path.join(DATA, "base_XAP")
REPLACE_XAP = os.path.join(DATA, "replace_XAP")
EN_MODEL = os.path.join(DATA, "en_extract", "data", "data", "model")
REPLACE_MODEL = os.path.join(DATA, "replace", "data", "data", "model")
EXT_XAP = (".NCGR", ".NCLR", ".NCER", ".NSCR")


def variantes(pasta, rel):
    """Arquivos de dados (NCGR/NCLR/NCER/NSCR) de uma imagem extraída."""
    tronco = rel[:-4]
    return [tronco + e for e in EXT_XAP if os.path.isfile(os.path.join(pasta, tronco + e))]


def copiar_se_mudou(origem, destino):
    if os.path.isfile(destino) and md5(destino) == md5(origem):
        return False
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.copy2(origem, destino)
    return True


def preparar_base_xap(m):
    """Pasta que a ferramenta usa como 'original' das imagens: japonês, mas com a paleta
    e o layout do inglês nas imagens que você edita partindo do inglês."""
    if not os.path.isdir(EXTRACT_XAP):
        return
    if not os.path.isdir(BASE_XAP):
        shutil.copytree(EXTRACT_XAP, BASE_XAP)
    for info in m.values():
        if info["tipo"] != "IMG":
            continue
        fonte = EN_XAP if info["base"] == "en" and os.path.isdir(EN_XAP) else EXTRACT_XAP
        for v in variantes(fonte, info["rel"]):
            copiar_se_mudou(os.path.join(fonte, v), os.path.join(BASE_XAP, v))


def modelo_de(rel):
    """Acha o .nsbmd de onde saiu a textura (nome = modelo + '_' + textura + '.png')."""
    melhor = None
    for pasta, _, arqs in os.walk(EN_MODEL):
        for a in arqs:
            if a.endswith(".nsbmd"):
                r = os.path.relpath(os.path.join(pasta, a), EN_MODEL).replace("\\", "/")
                if rel.startswith(r[:-6] + "_") and (melhor is None or len(r) > len(melhor)):
                    melhor = r
    return melhor


def sincronizar_imagens():
    """Monta o que vai pro repack: suas imagens editadas e mods (work_IMG/work_NSBMD) e,
    no modo inglês, os arquivos originais do patch em inglês (replace_XAP / replace\\model)."""
    m = carregar_manifesto()
    preparar_base_xap(m)
    ingles_on = modo_ingles()
    querer = {}  # "work:TIPO/rel" | "rxap:caminho" | "rmod:caminho" -> (origem, destino)
    for chave, info in m.items():
        if editada(info["tipo"], info["rel"], info):
            querer["work:" + chave] = (os.path.join(IMAGENS, info["tipo"], info["rel"]),
                                       os.path.join(DATA, TIPOS_IMG[info["tipo"]][2], info["rel"]))
    # skins ligadas: a de baixo entra primeiro, a de cima por último (a de cima vence)
    for nome in reversed(skins_ativas()):
        for tipo in TIPOS_IMG:
            raiz = os.path.join(SKINS, nome, tipo)
            for pasta, _, arqs in os.walk(raiz):
                for a in arqs:
                    if a.endswith(".png"):
                        rel = os.path.relpath(os.path.join(pasta, a), raiz).replace("\\", "/")
                        querer["work:" + tipo + "/" + rel] = (os.path.join(pasta, a),
                                                              os.path.join(DATA, TIPOS_IMG[tipo][2], rel))
    if ingles_on and os.path.isfile(EN_IMAGENS):
        with open(EN_IMAGENS, encoding="utf-8") as f:
            lista = json.load(f)
        for rel in lista.get("IMG", []):
            if "work:IMG/" + rel in querer:
                continue  # você editou: vale a sua versão
            for v in variantes(EN_XAP, rel):
                querer["rxap:" + v] = (os.path.join(EN_XAP, v), os.path.join(REPLACE_XAP, v))
        modelos_editados = {modelo_de(k[len("work:NSBMD/"):]) for k in querer if k.startswith("work:NSBMD/")}
        for rel in lista.get("NSBMD", []):
            mod_ = modelo_de(rel)
            if mod_ and mod_ not in modelos_editados:
                querer["rmod:" + mod_] = (os.path.join(EN_MODEL, mod_), os.path.join(REPLACE_MODEL, mod_))
    enviados = carregar_manifesto_envio()
    n_env = n_rem = 0
    for origem, destino in querer.values():
        n_env += copiar_se_mudou(origem, destino)
    destinos = {os.path.normcase(d) for _, d in querer.values()}
    for chave in enviados - set(querer):  # o script tinha mandado e não deve mais: tira
        tipo_dest, resto = chave.split(":", 1) if ":" in chave else ("work", chave)
        if tipo_dest == "work":
            tipo, rel = resto.split("/", 1)
            alvo = os.path.join(DATA, TIPOS_IMG[tipo][2], rel)
        elif tipo_dest == "rxap":
            alvo = os.path.join(REPLACE_XAP, resto)
        else:
            alvo = os.path.join(REPLACE_MODEL, resto)
        if os.path.isfile(alvo) and os.path.normcase(alvo) not in destinos:
            os.remove(alvo)
            n_rem += 1
    salvar_manifesto_envio(set(querer))
    if n_env or n_rem:
        print("  [imagens] %d arquivos atualizados pro repack, %d retirados" % (n_env, n_rem))


ENVIO = os.path.join(DATA, "auto_imagens_enviadas.json")


def carregar_manifesto_envio():
    if os.path.isfile(ENVIO):
        with open(ENVIO, encoding="utf-8") as f:
            return set(json.load(f))
    # compatibilidade com a versão anterior
    return {k for k, i in carregar_manifesto().items() if i.get("enviada")}


def salvar_manifesto_envio(chaves):
    with open(ENVIO, "w", encoding="utf-8") as f:
        json.dump(sorted(chaves), f, indent=0)


def galeria(m=None):
    m = m if m is not None else carregar_manifesto()
    if not m:
        print("  Nenhuma imagem preparada. Rode a opção 'imagens'.")
        return

    def url(p):
        return os.path.relpath(p, BASE).replace("\\", "/")
    feitas = sum(1 for i in m.values() if editada(i["tipo"], i["rel"], i))
    partes = ["<html><head><meta charset='utf-8'><title>Imagens</title><style>"
              "body{background:#1b1b1b;color:#ddd;font-family:sans-serif}"
              ".i{margin:14px 0;padding:10px;border-left:5px solid #c33;background:#222}.ok{border-color:#3b3}"
              ".c{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}.c div{text-align:center}"
              "img{image-rendering:pixelated;zoom:2;background:repeating-conic-gradient(#555 0 25%,#777 0 50%) 0 0/8px 8px}"
              "small{color:#999}</style></head><body>",
              "<h2>Imagens com texto: %d de %d editadas</h2>" % (feitas, len(m)),
              "<p>Edite os arquivos dentro da pasta <b>imagens\\</b> (mesmo tamanho, mesmas cores). "
              "Vermelho = ainda não editada. Rode 'aplicar' que só as editadas vão pro jogo.</p>"]
    for chave in sorted(m, key=lambda k: (editada(m[k]["tipo"], m[k]["rel"], m[k]), k)):
        i = m[chave]
        jp, en, _ = TIPOS_IMG[i["tipo"]]
        sua = os.path.join(IMAGENS, i["tipo"], i["rel"])
        ok = editada(i["tipo"], i["rel"], i)
        colunas = [("Japonês", os.path.join(DATA, jp, i["rel"])), ("Inglês (AGT)", os.path.join(DATA, en, i["rel"])),
                   ("Sua versão" if ok else "Sua versão (não editada)", sua)]
        partes.append("<div class='i%s'><small>imagens\\%s\\%s</small><div class='c'>%s</div></div>"
                      % (" ok" if ok else "", i["tipo"], i["rel"].replace("/", "\\"),
                         "".join("<div>%s<br><img src='%s'></div>" % (t, url(p)) for t, p in colunas if os.path.isfile(p))))
    partes.append("</body></html>")
    destino = os.path.join(BASE, "galeria_imagens.html")
    gravar(destino, partes)
    print("  [ok] galeria_imagens.html: %d de %d imagens editadas" % (feitas, len(m)))
    try:
        os.startfile(destino)
    except AttributeError:
        pass


# ================================================================ modo inglês / mods
def modo(qual):
    cfg = config(perguntar_melon=False)
    if qual == "ingles":
        if not os.path.isfile(EN_REF):
            print("  Rode a opção 'ingles' antes (puxar referência em inglês).")
            return
        cfg["base_ingles"] = True
        print("  [ok] modo INGLÊS: o que não estiver traduzido usa o texto e as imagens do patch em inglês.")
    elif qual == "portugues":
        cfg["base_ingles"] = False
        print("  [ok] modo PORTUGUÊS: só entra o que você traduziu (o resto fica em japonês).")
    else:
        print("  Use: modo ingles  ou  modo portugues")
        return
    salvar_config(cfg)
    print("  Rode 'tudo' pra gerar a ROM nesse modo.")


def todas_imagens():
    for tipo, (jp, en, _) in TIPOS_IMG.items():
        raiz = os.path.join(DATA, jp)
        for pasta, _, arqs in os.walk(raiz):
            for a in sorted(arqs):
                if a.endswith(".png"):
                    rel = os.path.relpath(os.path.join(pasta, a), raiz).replace("\\", "/")
                    yield tipo, rel


def origem_mod(tipo, rel):
    en = os.path.join(DATA, TIPOS_IMG[tipo][1], rel)
    return en if (modo_ingles() and os.path.isfile(en)) else os.path.join(DATA, TIPOS_IMG[tipo][0], rel)


def mod(termo):
    if not termo:
        print("  Use: python auto.py mod parte-do-nome   (veja os nomes no catalogo)")
        return
    n = 0
    for tipo, rel in todas_imagens():
        if termo.lower() in (tipo + "/" + rel).lower():
            destino = os.path.join(MODS, tipo, rel)
            if os.path.isfile(destino):
                print("  [já existe] mods\\%s\\%s" % (tipo, rel.replace("/", "\\")))
                continue
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(origem_mod(tipo, rel), destino)
            print("  [ok] mods\\%s\\%s" % (tipo, rel.replace("/", "\\")))
            n += 1
    print("  %d imagem(ns) copiada(s) pra pasta mods. Edite lá e rode 'tudo'." % n if n else
          "  Nada novo encontrado com '%s'." % termo)
    if n:
        try:
            os.startfile(MODS)
        except AttributeError:
            pass


def catalogo():
    lista = list(todas_imagens())
    if not lista:
        print("  Nenhuma imagem extraída (rode o extract com img e nsbmd marcados).")
        return

    def url(p):
        return os.path.relpath(p, BASE).replace("\\", "/")
    partes = ["<html><head><meta charset='utf-8'><title>Catálogo</title><style>"
              "body{background:#1b1b1b;color:#ddd;font-family:sans-serif}"
              "input{width:420px;padding:6px;font-size:16px}"
              ".g{display:flex;flex-wrap:wrap;gap:10px}.i{background:#252525;padding:6px;font-size:12px;max-width:540px}"
              ".m{outline:3px solid #3b3}"
              "img{image-rendering:pixelated;display:block;max-width:520px;"
              "background:repeating-conic-gradient(#555 0 25%,#777 0 50%) 0 0/8px 8px}</style></head><body>",
              "<h2>Catálogo de imagens (%d)</h2>" % len(lista),
              "<p>Ache a imagem (roupa, retrato, cenário) e copie pra pasta mods com:<br>"
              "<code>python auto.py mod NOME</code> (pode ser só parte do nome). Verde = já está em mods.</p>",
              "<input placeholder='filtrar pelo nome...' oninput=\"for(const d of document.querySelectorAll('.i'))"
              "d.style.display=d.dataset.n.includes(this.value.toLowerCase())?'':'none'\"><div class='g'>"]
    for tipo, rel in lista:
        em_mod = os.path.isfile(os.path.join(MODS, tipo, rel))
        nome = tipo + "/" + rel
        partes.append("<div class='i%s' data-n='%s'>%s<img loading='lazy' src='%s'></div>"
                      % (" m" if em_mod else "", nome.lower(), nome, url(origem_mod(tipo, rel))))
    partes.append("</div></body></html>")
    destino = os.path.join(BASE, "catalogo_imagens.html")
    gravar(destino, partes)
    print("  [ok] catalogo_imagens.html com %d imagens" % len(lista))
    try:
        os.startfile(destino)
    except AttributeError:
        pass


# ================================================================ paletas erradas
LISTA_PALETAS = os.path.join(BASE, "paletas_corrigidas.txt")


def ler_lista_paletas():
    """arquivo -> True (corrigir) / False (comentado)"""
    est = {}
    if os.path.isfile(LISTA_PALETAS):
        linhas, _ = ler(LISTA_PALETAS)
        for l in linhas:
            l = l.strip()
            if not l or l.startswith("##"):
                continue
            if l.startswith("#"):
                est[l.lstrip("#").strip()] = False
            else:
                est[l] = True
    return est


def paletas():
    print("  Testando as imagens (pode levar alguns minutos)...")
    if not rodar_pipenv(["auto_paletas.py"]):
        print("  [ERRO] o teste de paletas falhou (veja as mensagens acima)")
        return
    with open(os.path.join(DATA, "paleta_teste", "resultado.json"), encoding="utf-8") as f:
        res = json.load(f)
    est = ler_lista_paletas()
    linhas = ["## Imagens que usam a paleta corrigida. Sem '#' = corrigir, com '#' = deixar como está.",
              "## Depois de mudar, rode a opção de reextrair as imagens."]
    for r in res:
        ativo = est.get(r["arq"], r["sugere"])
        linhas.append(("" if ativo else "# ") + r["arq"])
    for arq, ativo in est.items():  # mantém escolhas manuais de arquivos fora do teste
        if not any(r["arq"] == arq for r in res):
            linhas.append(("" if ativo else "# ") + arq)
    gravar(LISTA_PALETAS, linhas, "\r\n")

    def url(p):
        return os.path.relpath(p, BASE).replace("\\", "/")
    partes = ["<html><head><meta charset='utf-8'><title>Paletas</title><style>"
              "body{background:#1b1b1b;color:#ddd;font-family:sans-serif}.i{margin:12px 0;padding:8px;background:#252525}"
              ".c{display:flex;gap:14px;flex-wrap:wrap}.s{outline:3px solid #3b3}"
              "img{image-rendering:pixelated;max-width:480px;background:repeating-conic-gradient(#555 0 25%,#777 0 50%) 0 0/8px 8px}"
              "</style></head><body><h2>Paletas: atual x corrigida</h2>"
              "<p>A borda verde marca a versão que vai ser usada (pelo arquivo <b>paletas_corrigidas.txt</b>). "
              "Se o script escolheu errado, edite o arquivo: sem '#' = corrigida, com '#' = atual.</p>"]
    for r in sorted(res, key=lambda r: (not est.get(r["arq"], r["sugere"]), r["arq"])):
        ativo = est.get(r["arq"], r["sugere"])
        a = os.path.join(DATA, "paleta_teste", "normal", r["png"])
        b = os.path.join(DATA, "paleta_teste", "corrigida", r["png"])
        partes.append("<div class='i'>%s<div class='c'><div class='%s'>Atual<br><img src='%s'></div>"
                      "<div class='%s'>Corrigida<br><img src='%s'></div></div></div>"
                      % (r["arq"], "" if ativo else "s", url(a), "s" if ativo else "", url(b)))
    partes.append("</body></html>")
    destino = os.path.join(BASE, "paletas.html")
    gravar(destino, partes)
    n = sum(1 for r in res if est.get(r["arq"], r["sugere"]))
    print("  [ok] %d imagens marcadas pra corrigir (veja paletas.html e paletas_corrigidas.txt)" % n)
    print("  Se estiver tudo certo, rode a opção de reextrair as imagens.")
    try:
        os.startfile(destino)
    except AttributeError:
        pass


def reextrair():
    print("  Reextraindo as imagens japonesas com as paletas corrigidas...")
    if not rodar_pipenv(["tool.py", "extract", "--img"]):
        print("  [ERRO] a reextração falhou")
        return
    cfg = config(perguntar_melon=False)
    if os.path.isdir(os.path.join(DATA, "en_extract")) and os.path.isfile(cfg.get("rom_ingles", "")):
        print("  Reextraindo as imagens em inglês...")
        rodar_pipenv(["auto_ingles.py", cfg["rom_ingles"], "--so-imagens"])
    est = {a for a, v in ler_lista_paletas().items() if v}
    afetados = []
    raiz = os.path.join(MODS, "IMG")
    for pasta, _, arqs in os.walk(raiz):
        for a in arqs:
            rel = os.path.relpath(os.path.join(pasta, a), raiz).replace("\\", "/")
            if rel[:-4] + ".NCGR" in est:
                afetados.append(rel)
    if afetados:
        print("  [atenção] estes mods foram copiados com as cores erradas; apague e copie de novo com a opção 'mod':")
        for a in afetados:
            print("     " + os.path.relpath(os.path.join(MODS, "IMG", a), BASE))
    print("  [ok] pronto. Abra o catálogo de novo pra conferir.")


# ================================================================ replicar edição
def replicar(args):
    nomes = [a for a in args if not a.startswith("--")]
    if len(nomes) < 2:
        print("  Use: python auto.py replicar IMAGEM_EDITADA PADRAO   (ex.: replicar char003u char003)")
        return
    extra = ["--forcar"] if "--forcar" in args else []
    extra.append("--pasta=" + os.path.relpath(os.path.join(MODS, "IMG"), BASE).replace("\\", "/") + "/")
    if not rodar_pipenv(["auto_cores.py", "replicar", nomes[0], nomes[1]] + extra):
        return
    p = os.path.join(DATA, "replicar.json")
    if not os.path.isfile(p):
        return
    with open(p, encoding="utf-8") as f:
        res = json.load(f)

    def url(c):
        return os.path.relpath(c, BASE).replace("\\", "/")
    partes = ["<html><head><meta charset='utf-8'><title>Replicar</title><style>"
              "body{background:#1b1b1b;color:#ddd;font-family:sans-serif}.i{margin:12px 0;padding:8px;background:#252525}"
              ".c{display:flex;gap:14px;flex-wrap:wrap}img{image-rendering:pixelated;max-width:480px;"
              "background:repeating-conic-gradient(#555 0 25%,#777 0 50%) 0 0/8px 8px}</style></head><body>",
              "<h2>Edição de %s replicada</h2><p>Confira cada uma. Se alguma ficou errada (ex.: pintou o olho "
              "junto com a roupa), apague o arquivo dela na pasta mods ou corrija no GIMP.</p>" % res["origem"]]
    for alvo, n in res["feitos"]:
        partes.append("<div class='i'>mods\\IMG\\%s &middot; %d pixels<div class='c'><div>Original<br><img src='%s'></div>"
                      "<div>Nova<br><img src='%s'></div></div></div>"
                      % (alvo.replace("/", "\\"), n, url(os.path.join(DATA, "out_IMG", alvo)),
                         url(os.path.join(MODS, "IMG", alvo))))
    partes.append("</body></html>")
    destino = os.path.join(BASE, "replicar.html")
    gravar(destino, partes)
    try:
        os.startfile(destino)
    except AttributeError:
        pass


# ================================================================ skins
def ler_info_skin(nome):
    p = os.path.join(SKINS, nome, "skin.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"nome": nome, "descricao": ""}


def gravar_info_skin(nome, info):
    os.makedirs(os.path.join(SKINS, nome), exist_ok=True)
    with open(os.path.join(SKINS, nome, "skin.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1, ensure_ascii=False)


def migrar_mods():
    """A pasta mods antiga vira a skin 'Meus mods'."""
    antiga = os.path.join(BASE, "mods")
    cfg = config(perguntar_melon=False)
    if not os.path.isdir(SKINS):
        os.makedirs(SKINS, exist_ok=True)
        destino = os.path.join(SKINS, "Meus mods")
        if os.path.isdir(antiga):
            shutil.move(antiga, destino)
        os.makedirs(destino, exist_ok=True)
        gravar_info_skin("Meus mods", {"nome": "Meus mods", "descricao": "Os mods que você já tinha feito."})
        cfg.setdefault("skins_ativas", ["Meus mods"])
        cfg.setdefault("skin_atual", "Meus mods")
        salvar_config(cfg)
    existentes = [d for d in sorted(os.listdir(SKINS)) if os.path.isdir(os.path.join(SKINS, d))]
    if not existentes:
        os.makedirs(os.path.join(SKINS, "Meus mods"), exist_ok=True)
        existentes = ["Meus mods"]
    mudou = False
    ativas = [n for n in cfg.get("skins_ativas", []) if n in existentes]
    if ativas != cfg.get("skins_ativas"):
        cfg["skins_ativas"] = ativas
        mudou = True
    if cfg.get("skin_atual") not in existentes:
        cfg["skin_atual"] = ativas[0] if ativas else existentes[0]
        mudou = True
    if mudou:
        salvar_config(cfg)


def atualizar_mods():
    global MODS
    migrar_mods()
    MODS = os.path.join(SKINS, config(perguntar_melon=False)["skin_atual"])
    os.makedirs(MODS, exist_ok=True)
    return MODS


def skins_ativas():
    migrar_mods()
    return list(config(perguntar_melon=False).get("skins_ativas", []))


def contar_pngs(pasta):
    return sum(len([a for a in fs if a.endswith(".png")]) for _, _, fs in os.walk(pasta)) if os.path.isdir(pasta) else 0


def listar_skins():
    migrar_mods()
    cfg = config(perguntar_melon=False)
    ativas = cfg.get("skins_ativas", [])
    todas = [d for d in sorted(os.listdir(SKINS)) if os.path.isdir(os.path.join(SKINS, d))]
    ordem = ativas + [d for d in todas if d not in ativas]
    out = []
    for nome in ordem:
        info = ler_info_skin(nome)
        capa = ""
        for tipo in TIPOS_IMG:
            for pasta, _, arqs in os.walk(os.path.join(SKINS, nome, tipo)):
                pngs = sorted(a for a in arqs if a.endswith(".png"))
                if pngs and not capa:
                    capa = os.path.relpath(os.path.join(pasta, pngs[0]), BASE).replace("\\", "/")
        out.append({"nome": nome, "descricao": info.get("descricao", ""), "ativa": nome in ativas,
                    "atual": nome == cfg.get("skin_atual"), "posicao": ativas.index(nome) + 1 if nome in ativas else 0,
                    "imagens": contar_pngs(os.path.join(SKINS, nome)), "capa": capa,
                    "pasta": os.path.relpath(os.path.join(SKINS, nome), BASE).replace("\\", "/")})
    return out


PROIBIDOS = '<>:"/\\|?*'


def nome_valido(nome):
    nome = (nome or "").strip().strip(".")
    if not nome or any(c in PROIBIDOS for c in nome) or len(nome) > 60:
        raise ValueError("Nome inválido. Não use estes caracteres: < > : \" / \\ | ? *")
    return nome


def imagens_da_skin(nome):
    out = []
    for tipo in TIPOS_IMG:
        raiz = os.path.join(SKINS, nome, tipo)
        for pasta, _, arqs in os.walk(raiz):
            for a in sorted(arqs):
                if a.endswith(".png"):
                    out.append(tipo + "/" + os.path.relpath(os.path.join(pasta, a), raiz).replace("\\", "/"))
    return sorted(out)


def skin(acao, nome="", novo="", descricao="", graus=0, arquivo="", itens=None, substituir=False):
    """Todas as operações de skin. Devolve uma mensagem pro usuário."""
    migrar_mods()
    cfg = config(perguntar_melon=False)
    ativas = cfg.get("skins_ativas", [])
    existe = lambda n: os.path.isdir(os.path.join(SKINS, n))
    if acao == "criar":
        novo = nome_valido(novo)
        if existe(novo):
            raise ValueError("Já existe uma skin chamada %s." % novo)
        gravar_info_skin(novo, {"nome": novo, "descricao": descricao})
        cfg["skins_ativas"] = [novo] + ativas
        cfg["skin_atual"] = novo
        salvar_config(cfg)
        atualizar_mods()
        return "Skin %s criada, ligada e escolhida pra receber os próximos mods." % novo
    if not existe(nome):
        raise ValueError("A skin %s não existe." % nome)
    if acao == "ligar":
        if nome not in ativas:
            cfg["skins_ativas"] = [nome] + ativas
        msg = "Skin %s ligada." % nome
    elif acao == "desligar":
        cfg["skins_ativas"] = [n for n in ativas if n != nome]
        msg = "Skin %s desligada." % nome
    elif acao == "atual":
        cfg["skin_atual"] = nome
        msg = "Os próximos mods vão pra skin %s." % nome
    elif acao in ("subir", "descer"):
        if nome in ativas:
            i = ativas.index(nome)
            j = i - 1 if acao == "subir" else i + 1
            if 0 <= j < len(ativas):
                ativas[i], ativas[j] = ativas[j], ativas[i]
            cfg["skins_ativas"] = ativas
        msg = "Ordem atualizada."
    elif acao == "renomear":
        novo = nome_valido(novo)
        if novo != nome and existe(novo):
            raise ValueError("Já existe uma skin chamada %s." % novo)
        os.rename(os.path.join(SKINS, nome), os.path.join(SKINS, novo))
        info = ler_info_skin(novo)
        info["nome"] = novo
        if descricao is not None:
            info["descricao"] = descricao
        gravar_info_skin(novo, info)
        cfg["skins_ativas"] = [novo if n == nome else n for n in ativas]
        if cfg.get("skin_atual") == nome:
            cfg["skin_atual"] = novo
        msg = "Skin salva como %s." % novo
    elif acao == "duplicar":
        novo = nome_valido(novo or nome + " (cópia)")
        if existe(novo):
            raise ValueError("Já existe uma skin chamada %s." % novo)
        shutil.copytree(os.path.join(SKINS, nome), os.path.join(SKINS, novo))
        info = ler_info_skin(novo)
        info["nome"] = novo
        gravar_info_skin(novo, info)
        msg = "Cópia criada: %s (desligada)." % novo
    elif acao == "apagar":
        lixo = os.path.join(BACKUPS, "skins_apagadas")
        os.makedirs(lixo, exist_ok=True)
        destino = os.path.join(lixo, "%s_%s" % (nome, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
        shutil.move(os.path.join(SKINS, nome), destino)
        cfg["skins_ativas"] = [n for n in ativas if n != nome]
        msg = "Skin %s apagada. Uma cópia ficou em backups\\skins_apagadas, se precisar recuperar." % nome
    elif acao == "exportar":
        pasta = os.path.join(LANCAMENTO, "skins")
        os.makedirs(pasta, exist_ok=True)
        base = os.path.join(pasta, nome)
        shutil.make_archive(base, "zip", SKINS, nome)
        msg = "Skin exportada em lancamento\\skins\\%s.zip" % nome
    elif acao == "variar":
        graus = int(graus)
        novo = nome_valido(novo or "%s (tom %+d)" % (nome, graus))
        if existe(novo):
            raise ValueError("Já existe uma skin chamada %s." % novo)
        if not rodar_pipenv(["auto_cores.py", "variar", os.path.relpath(os.path.join(SKINS, nome), BASE),
                             os.path.relpath(os.path.join(SKINS, novo), BASE), str(graus)]):
            raise RuntimeError("Não consegui gerar a variação (veja o registro).")
        info = ler_info_skin(nome)
        gravar_info_skin(novo, {"nome": novo, "descricao": "Variação de %s com o tom girado %+d graus." % (nome, graus)})
        msg = "Variação criada: %s (desligada). Ligue pra testar." % novo
    elif acao == "copiar":
        # traz imagens de outra skin (nome = origem) pra skin 'novo' (padrão: a atual)
        destino = novo or cfg.get("skin_atual")
        if not existe(destino):
            raise ValueError("A skin %s não existe." % destino)
        if destino == nome:
            raise ValueError("Escolha uma skin diferente da que recebe as imagens.")
        n = pulei = 0
        for item in itens or imagens_da_skin(nome):
            tipo, rel = item.split("/", 1)
            src = os.path.join(SKINS, nome, tipo, rel)
            dst = os.path.join(SKINS, destino, tipo, rel)
            if not os.path.isfile(src) or ".." in rel:
                continue
            if os.path.isfile(dst) and not substituir:
                pulei += 1
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
        msg = "%d imagem(ns) copiada(s) de %s pra %s." % (n, nome, destino)
        if pulei:
            msg += " %d já existiam lá e foram mantidas." % pulei
    elif acao == "descricao":
        info = ler_info_skin(nome)
        info["descricao"] = descricao
        gravar_info_skin(nome, info)
        msg = "Descrição salva."
    else:
        raise ValueError("Ação de skin desconhecida.")
    salvar_config(cfg)
    atualizar_mods()
    return msg


def importar_skin(arquivo):
    import zipfile
    arquivo = arquivo.strip().strip('"')
    if not os.path.isfile(arquivo) or not zipfile.is_zipfile(arquivo):
        raise ValueError("Escolha um arquivo .zip de skin.")
    migrar_mods()
    with zipfile.ZipFile(arquivo) as z:
        nomes = [n for n in z.namelist() if not n.endswith("/")]
        raiz = {n.split("/")[0] for n in nomes}
        nome = raiz.pop() if len(raiz) == 1 else os.path.splitext(os.path.basename(arquivo))[0]
        nome = nome_valido(nome)
        destino = os.path.join(SKINS, nome)
        n = 2
        while os.path.isdir(destino):
            destino = os.path.join(SKINS, "%s (%d)" % (nome, n))
            n += 1
        os.makedirs(destino)
        for item in nomes:
            partes = item.split("/")
            if len(raiz) == 0 and len(partes) > 1:
                partes = partes[1:]  # tira a pasta raiz do zip
            rel = os.path.normpath(os.path.join(*partes))
            if rel.startswith("..") or os.path.isabs(rel):
                continue  # ignora caminhos perigosos
            if not (rel.lower().endswith(".png") or rel.endswith("skin.json")):
                continue  # skins só trazem imagens
            alvo = os.path.join(destino, rel)
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            with z.open(item) as src, open(alvo, "wb") as dst:
                shutil.copyfileobj(src, dst)
    final = os.path.basename(destino)
    info = ler_info_skin(final)
    info["nome"] = final
    gravar_info_skin(final, info)
    return "Skin %s importada (desligada). Ligue na lista pra usar." % final


# ================================================================ lançamento
def lancar():
    if not os.path.isfile(PATCH):
        print("  [ERRO] data\\patch.xdelta não encontrado. Rode o repack antes.")
        return
    cfg = config(perguntar_melon=False)
    versao = cfg.get("versao", 0) + 1
    cfg["versao"] = versao
    salvar_config(cfg)
    os.makedirs(LANCAMENTO, exist_ok=True)
    nome = "KiraGame-PTBR-v0.%d.xdelta" % versao
    shutil.copy2(PATCH, os.path.join(LANCAMENTO, nome))
    progresso = []
    for n in ARQUIVOS:
        r = contar(n)
        if r:
            progresso.append("  %s: %d de %d frases" % (n, r[0], r[1]))
    leia = """Death Note: Kira Game - Tradução PT-BR (v0.%d)
Data: %s

Progresso:
%s

COMO APLICAR
1. Tenha a ROM japonesa original, feita do seu próprio cartucho:
   Death Note - Kira Game (Japan)  CRC32: 144938ea
2. Abra https://www.marcrobledo.com/RomPatcher.js/
3. Em "ROM file" escolha a ROM original, em "Patch file" escolha %s
4. Clique em "Apply patch" e jogue o arquivo gerado no emulador ou flashcart.

Não use uma ROM já traduzida para inglês: o patch é feito sobre a japonesa.

CRÉDITOS
Ferramentas de tradução: AGT Team / Illidan (KiraGameTranslation, licença MIT).
Este patch não inclui o jogo. Death Note é propriedade de seus respectivos donos.
""" % (versao, datetime.date.today().strftime("%d/%m/%Y"), "\n".join(progresso), nome)
    gravar(os.path.join(LANCAMENTO, "LEIAME-v0.%d.txt" % versao), leia.split("\n"), "\r\n")
    print("  [ok] lancamento\\%s e LEIAME-v0.%d.txt criados" % (nome, versao))


# ================================================================ main
def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "ajuda"
    flags = set(args[1:])
    limpo = "--limpo" in flags
    os.chdir(BASE)
    acoes = {
        "planilha": lambda: planilha("--refazer" in flags),
        "ingles": ingles,
        "aplicar": aplicar,
        "repack": repack,
        "jogar": lambda: jogar(limpo),
        "tudo": lambda: (aplicar(), repack() and jogar(limpo)),
        "vigiar": lambda: vigiar(limpo),
        "previa": previa,
        "imagens": lambda: imagens("jp" if "--base-jp" in flags else None),
        "galeria": galeria,
        "modo": lambda: modo(args[1] if len(args) > 1 else ""),
        "catalogo": catalogo,
        "paletas": paletas,
        "reextrair": reextrair,
        "replicar": lambda: replicar(args[1:]),
        "mod": lambda: mod(" ".join(args[1:])),
        "lancar": lancar,
        "status": status,
    }
    if cmd in acoes:
        acoes[cmd]()
    else:
        print(__doc__)


try:
    atualizar_mods()
except Exception as _e:  # sem permissão de escrita etc.: segue com a pasta mods antiga
    print("  [aviso] skins:", _e)

if __name__ == "__main__":
    main()

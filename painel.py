# -*- coding: utf-8 -*-
"""Painel do Kira Game (versão 1): tudo num lugar só, no navegador.

Fica na pasta principal (KiraGameTranslation-master), junto com o auto.py.
Abra com:  python painel.py   (ou dois cliques no painel.bat)
"""
import base64
import io
import json
import re
import mimetypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def achar_base():
    """Pasta do projeto: ao lado do painel.py, ou do .exe (ou a pasta de cima, se o .exe
    estiver numa subpasta como PainelKiraGame)."""
    if os.environ.get("KIRA_BASE"):
        return os.environ["KIRA_BASE"]
    if getattr(sys, "frozen", False):
        aqui = os.path.dirname(os.path.abspath(sys.executable))
        for p in (aqui, os.path.dirname(aqui)):
            if os.path.isfile(os.path.join(p, "tool.py")):
                return p
        return aqui
    return os.path.dirname(os.path.abspath(__file__))


BASE = achar_base()
os.environ["KIRA_BASE"] = BASE
os.chdir(BASE)
sys.path.insert(0, BASE)


def usar_painel_da_pasta():
    """No .exe: se existir um painel.py na pasta do projeto, é ele que roda (e não a cópia
    guardada dentro do executável). Assim, pra atualizar basta trocar os arquivos."""
    if not getattr(sys, "frozen", False) or globals().get("_PAINEL_DA_PASTA"):
        return
    externo = os.path.join(BASE, "painel.py")
    if not os.path.isfile(externo):
        return
    import runpy
    try:
        runpy.run_path(externo, init_globals={"_PAINEL_DA_PASTA": True}, run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        import traceback as tb
        print("O painel.py da pasta deu erro; abrindo a versão que veio dentro do programa.\n")
        tb.print_exc()
        return
    sys.exit(0)


usar_painel_da_pasta()


def rodar_script():
    """Modo usado pelo .exe: 'PainelKiraGame.exe --rodar tool.py repack' roda o script do
    projeto com o Python e as bibliotecas que vêm dentro do executável."""
    import runpy
    i = sys.argv.index("--rodar")
    script, args = sys.argv[i + 1], sys.argv[i + 2:]
    sys.argv = [script] + args
    try:
        runpy.run_path(os.path.join(BASE, script), run_name="__main__")
    except SystemExit as e:
        sys.exit(e.code if isinstance(e.code, int) else (0 if e.code is None else 1))
    sys.exit(0)


if "--rodar" in sys.argv:
    rodar_script()

import auto  # noqa: E402

PORTA = 8765
VERSAO = "1.6"

# ================================================================ log e tarefas
LOG = []
LOG_LOCK = threading.Lock()


def eh_barra(t):
    return "it/s]" in t or "s/it]" in t or ("%|" in t and "|" in t)


LOG_VERSAO = [0]  # muda quando uma linha antiga é substituída (barra de progresso)


def log(txt):
    with LOG_LOCK:
        for parte in str(txt).replace("\r\n", "\n").split("\n"):
            parte = parte.split("\r")[-1]  # barras de progresso: fica só o último estado
            if eh_barra(parte) and LOG and eh_barra(LOG[-1]):
                LOG[-1] = parte  # atualiza a mesma linha em vez de empilhar
                LOG_VERSAO[0] += 1
                continue
            if parte.strip() or (LOG and LOG[-1] != ""):
                LOG.append(parte)
        if len(LOG) > 5000:
            del LOG[:1000]


class Saida(io.TextIOBase):
    def write(self, s):
        if s:
            log(s.rstrip("\n")) if s.strip() else None
        return len(s)

    def flush(self):
        pass


sys.stdout = Saida()
sys.stderr = Saida()


def rodar_pipenv_log(args):
    """Igual ao auto.rodar_pipenv, mas mandando a saída pro painel."""
    for pre in auto.comandos_python():
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            p = subprocess.Popen(pre + args, cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError:
            continue
        buf = b""
        while True:
            ch = p.stdout.read(1)
            if not ch:
                break
            if ch in (b"\n", b"\r"):
                if buf:
                    log(buf.decode("utf-8", "replace"))
                buf = b""
            else:
                buf += ch
        if buf:
            log(buf.decode("utf-8", "replace"))
        return p.wait() == 0
    log("  [ERRO] pipenv não encontrado. Rode o run_windows.bat uma vez antes.")
    return False


auto.rodar_pipenv = rodar_pipenv_log


def sem_input(msg=""):
    raise RuntimeError("Falta configurar um caminho na aba Configurações (%s)" % msg.strip())


auto.input = sem_input  # o painel não pergunta no terminal
if not hasattr(os, "startfile"):
    os.startfile = lambda p: None

TAREFA = {"nome": None, "inicio": 0}
TAREFA_LOCK = threading.Lock()


def executar(nome, func):
    with TAREFA_LOCK:
        if TAREFA["nome"]:
            return False
        TAREFA.update(nome=nome, inicio=time.time())

    def corpo():
        log("")
        log("===== %s =====" % nome)
        try:
            func()
        except Exception as e:
            log("  [ERRO] %s" % e)
            log(traceback.format_exc())
        finally:
            log("===== fim: %s (%.0fs) =====" % (nome, time.time() - TAREFA["inicio"]))
            with TAREFA_LOCK:
                TAREFA["nome"] = None
    threading.Thread(target=corpo, daemon=True).start()
    return True


# ================================================================ modo automático
VIGIA = {"ligado": False, "limpo": False}


def laco_vigia():
    alvos = [os.path.join(auto.PASTA, n + ".txt") for n in auto.ARQUIVOS] + [auto.GLOSSARIO]

    def marcas():
        ms = [os.path.getmtime(p) if os.path.isfile(p) else 0 for p in alvos]
        for pasta in (auto.IMAGENS, auto.SKINS):
            for raiz, _, arqs in os.walk(pasta):
                ms += [os.path.getmtime(os.path.join(raiz, a)) for a in arqs if a.endswith(".png")]
        return ms
    proc = [None]

    def ciclo():
        auto.aplicar()
        if proc[0] is not None and proc[0].poll() is None:
            proc[0].terminate()
        if auto.repack():
            proc[0] = auto.jogar(VIGIA["limpo"])
    ultimo = None
    while VIGIA["ligado"]:
        agora = marcas()
        if agora != ultimo and not TAREFA["nome"]:
            if ultimo is not None:
                time.sleep(0.5)
            executar("Modo automático: atualizando o jogo", ciclo)
            while TAREFA["nome"]:
                time.sleep(0.5)
            ultimo = marcas()
        time.sleep(1.5)
    log("  Modo automático desligado.")


# ================================================================ textos
def itens_planilha(nome):
    p = os.path.join(auto.PASTA, nome + ".txt")
    if not os.path.isfile(p):
        return []
    linhas, _ = auto.ler(p)
    out, secao, en = [], "", ""
    for i, linha in enumerate(linhas):
        if linha.startswith("# ====="):
            secao = linha.strip("# =").strip()
            continue
        if linha.startswith("#EN: "):
            en = linha[5:]
            continue
        m = auto.ITEM.match(linha)
        if m:
            chave, trad = auto.separar(m.group(2))
            if chave is not None:
                out.append({"idx": i, "num": int(m.group(1)), "jp": chave, "en": en, "pt": trad, "secao": secao})
        en = ""
    return out


def salvar_texto(nome, idx, pt):
    p = os.path.join(auto.PASTA, nome + ".txt")
    linhas, nl = auto.ler(p)
    m = auto.ITEM.match(linhas[idx]) if 0 <= idx < len(linhas) else None
    if not m:
        return {"ok": False, "erros": ["linha não encontrada; recrie a planilha"]}
    chave, _ = auto.separar(m.group(2))
    pt = pt.replace("\r", "").replace("\n", "|")
    _, erros, avisos = auto.validar(chave, pt, nome)
    if pt and pt != "!":
        avisos += auto.checar_glossario(chave, pt, auto.carregar_glossario())
    linhas[idx] = "@%s %s=%s" % (m.group(1), chave, pt)
    auto.gravar(p, linhas, nl)
    return {"ok": True, "erros": erros, "avisos": avisos}


def previa_texto(nome, txt):
    caminho = next((f for f in auto.FONTES if os.path.isfile(f)), None)
    if caminho is None or not txt:
        return None
    fonte = auto.Fonte(caminho)
    limpo = auto.CODIGOS.sub("", txt)
    for a, b in auto.TROCAS.items():
        limpo = limpo.replace(a, b)
    formatado = limpo.startswith("{{")
    if formatado:
        limpo = limpo[2:]
    centro = limpo.startswith("<<")
    if centro:
        limpo = limpo[2:]
    limite = auto.LARGURA_TELA if (centro or nome == "bin") else auto.LARGURA_FALA
    if not formatado and (centro or (nome == "scenario" and "|" not in limpo)):
        linhas = auto.quebrar(fonte, limpo, limite)
    else:
        linhas = limpo.split("|")
    maior = max(sum(fonte.avanco(c) for c in l) for l in linhas)
    if centro:
        linhas = ["<<" + l for l in linhas]
    return {"png": base64.b64encode(auto.desenhar(fonte, linhas, limite)).decode(),
            "largura": maior, "limite": limite, "linhas": len(linhas),
            "ruim": maior > limite or (nome == "scenario" and len(linhas) > 3)}


# ================================================================ imagens
def info_imagem(tipo, rel):
    for item in lista_imagens(rel):
        if item["tipo"] == tipo and item["rel"] == rel:
            return item
    return None


def rel_base(p):
    return os.path.relpath(p, BASE).replace("\\", "/")


def lista_imagens(filtro):
    auto.atualizar_mods()
    atual = os.path.basename(auto.MODS)
    outras = [s_["nome"] for s_ in auto.listar_skins() if s_["nome"] != atual]
    m = auto.carregar_manifesto()
    com_texto = set()
    if os.path.isfile(auto.EN_IMAGENS):
        with open(auto.EN_IMAGENS, encoding="utf-8") as f:
            for tipo, rels in json.load(f).items():
                com_texto |= {tipo + "/" + r for r in rels}
    out = []
    for tipo, rel in auto.todas_imagens():
        chave = tipo + "/" + rel
        if filtro and filtro.lower() not in chave.lower():
            continue
        jp, en, _ = auto.TIPOS_IMG[tipo]
        item = {"tipo": tipo, "rel": rel,
                "jp": "data/%s/%s" % (jp, rel),
                "en": "data/%s/%s" % (en, rel) if os.path.isfile(os.path.join(auto.DATA, en, rel)) else "",
                "mod": rel_base(os.path.join(auto.MODS, tipo, rel)) if os.path.isfile(os.path.join(auto.MODS, tipo, rel)) else "",
                "outras": [n for n in outras if os.path.isfile(os.path.join(auto.SKINS, n, tipo, rel))],
                "outra_src": "",
                "texto": chave in com_texto,
                "trad": "", "trad_editada": False}
        if item["outras"]:
            item["outra_src"] = rel_base(os.path.join(auto.SKINS, item["outras"][0], tipo, rel))
        if chave in m:
            item["trad"] = "imagens/%s/%s" % (tipo, rel)
            item["trad_editada"] = auto.editada(tipo, rel, m[chave])
        out.append(item)
    return out


def copiar_mod(tipo, rel, original=False):
    """Adiciona a imagem à skin atual. Se você já modificou essa imagem em outra skin,
    copia a versão modificada (a da skin mais acima), a não ser que peça o original."""
    auto.atualizar_mods()
    destino = os.path.join(auto.MODS, tipo, rel)
    if os.path.isfile(destino):
        return rel_base(destino), ""
    origem, de = auto.origem_mod(tipo, rel), ""
    if not original:
        atual = os.path.basename(auto.MODS)
        for sk in auto.listar_skins():
            p = os.path.join(auto.SKINS, sk["nome"], tipo, rel)
            if sk["nome"] != atual and os.path.isfile(p):
                origem, de = p, sk["nome"]
                break
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.copy2(origem, destino)
    return rel_base(destino), de


MATERIAIS = os.path.join(BASE, "materiais")


def referencia(tipo, rel):
    """Cópia da imagem original pra pasta materiais (fora das skins: nunca vai pro jogo)."""
    destino = os.path.join(MATERIAIS, tipo, rel)
    if not os.path.isfile(destino):
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        shutil.copy2(auto.origem_mod(tipo, rel), destino)
    return rel_base(destino)


def abrir(caminho, gimp=False):
    p = os.path.normpath(os.path.join(BASE, caminho))
    if not p.startswith(BASE) or not os.path.exists(p):
        return False
    cfg = auto.config(perguntar_melon=False)
    if gimp and os.path.isfile(cfg.get("gimp", "")):
        subprocess.Popen([cfg["gimp"], p])
    else:
        os.startfile(p)
    return True


# ================================================================ prévia no DS
def rodar_cores(comando, tipo, rel, png, original=""):
    """Roda 'auto_cores.py previa|limpar' e devolve (resumo, caminho_base_relativo)."""
    if tipo != "IMG" or not rel.lower().endswith(".png"):
        return {"ok": False, "msg": "Isso funciona com as imagens da pasta IMG."}, ""
    arq = os.path.normpath(os.path.join(BASE, png))
    if not arq.startswith(BASE) or not os.path.isfile(arq):
        return {"ok": False, "msg": "O arquivo do mod não existe mais."}, ""
    pasta = os.path.join(auto.DATA, "previa_ds")
    os.makedirs(pasta, exist_ok=True)
    saida = os.path.join(pasta, re.sub(r"[^A-Za-z0-9_-]", "_", rel[:-4]))
    for ext in ("_ds.png", "_marcas.png", "_limpa.png", "_limpa_ds.png", "_limpa_marcas.png", ".json"):
        if os.path.isfile(saida + ext):
            os.remove(saida + ext)
    args = ["auto_cores.py", comando, rel, arq, saida]
    if original:
        orig = os.path.normpath(os.path.join(BASE, original))
        if orig.startswith(BASE) and os.path.isfile(orig):
            args.append(orig)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    saida_txt = ""
    for pre in auto.comandos_python():
        try:
            r = subprocess.run(pre + args, cwd=BASE, capture_output=True, env=env, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "Demorou demais. Tente de novo."}, ""
        saida_txt = (r.stdout + r.stderr).decode("utf-8", "replace")
        break
    else:
        return {"ok": False, "msg": "pipenv não encontrado. Rode o run_windows.bat uma vez antes."}, ""
    if not os.path.isfile(saida + ".json"):
        log("  [%s] %s" % (comando, saida_txt.strip()[-800:]))
        return {"ok": False, "msg": "Não consegui analisar a imagem. Veja o registro."}, ""
    with open(saida + ".json", encoding="utf-8") as f:
        resumo = json.load(f)
    return resumo, os.path.relpath(saida, BASE).replace("\\", "/")


def previa_ds(tipo, rel, png, original=""):
    resumo, base = rodar_cores("previa", tipo, rel, png, original)
    if resumo.get("ok"):
        resumo["ds"] = base + "_ds.png"
        resumo["marcas"] = base + "_marcas.png"
    return resumo


def limpar_bordas(tipo, rel, png, original=""):
    """Gera a cópia limpa pra comparar. Não mexe no mod."""
    resumo, base = rodar_cores("limpar", tipo, rel, png, original)
    if resumo.get("ok"):
        resumo.update(antes=base + "_ds.png", depois=base + "_limpa_ds.png",
                      marcas=base + "_limpa_marcas.png", limpa=base + "_limpa.png")
    return resumo


def aplicar_limpeza(png, limpa):
    """Troca o mod pela cópia limpa, guardando a versão anterior em backups/limpeza."""
    arq = os.path.normpath(os.path.join(BASE, png))
    nova = os.path.normpath(os.path.join(BASE, limpa))
    pasta_previa = os.path.join(auto.DATA, "previa_ds")
    if not arq.startswith(os.path.join(BASE, "skins")) or not os.path.isfile(arq) \
       or not nova.startswith(pasta_previa) or not nova.endswith("_limpa.png") or not os.path.isfile(nova):
        return {"ok": False, "msg": "A cópia limpa não existe mais. Clique em Limpar bordas de novo."}
    guarda = os.path.join(auto.BACKUPS, "limpeza")
    os.makedirs(guarda, exist_ok=True)
    nome = "%s_%s" % (time.strftime("%Y-%m-%d_%H-%M-%S"), os.path.basename(arq))
    shutil.copy2(arq, os.path.join(guarda, nome))
    shutil.copy2(nova, arq)
    log("Bordas limpas em %s (a versão anterior está em backups\\limpeza\\%s)" % (os.path.basename(arq), nome))
    return {"ok": True, "guardado": "backups/limpeza/" + nome}


# ================================================================ status
def status_geral():
    cfg = auto.config(perguntar_melon=False)
    prog = {}
    for nome in auto.ARQUIVOS:
        r = auto.contar(nome)
        if r:
            prog[nome] = {"feitas": r[0], "total": r[1]}
    m = auto.carregar_manifesto()
    auto.atualizar_mods()
    n_mods = auto.contar_pngs(auto.MODS)
    checar = {
        "ROM japonesa (dn1.nds)": os.path.isfile(os.path.join(BASE, "dn1.nds")),
        "Jogo extraído (data/extract)": os.path.isdir(os.path.join(auto.DATA, "extract")),
        "Planilhas de tradução": os.path.isdir(auto.PASTA),
        "Fonte com acentos": os.path.isfile(auto.FONTES[0]),
        "Referência em inglês": os.path.isfile(auto.EN_REF),
        "Imagens com texto preparadas": bool(m),
        "melonDS configurado": os.path.isfile(cfg.get("melonds", "")),
        "ROM gerada (dn1_patched.nds)": os.path.isfile(auto.ROM),
    }
    return {
        "versao": VERSAO, "tarefa": TAREFA["nome"], "vigia": VIGIA["ligado"], "limpo": VIGIA["limpo"],
        "modo_ingles": auto.modo_ingles(), "progresso": prog,
        "imagens": {"editadas": sum(1 for i in m.values() if auto.editada(i["tipo"], i["rel"], i)), "total": len(m)},
        "mods": n_mods, "checar": checar, "skin_atual": os.path.basename(auto.MODS),
        "skins_ligadas": len(auto.skins_ativas()),
        "ultimo_backup": auto.ultimo_backup_diario(),
        "config": {k: cfg.get(k, "") for k in ("melonds", "rom_ingles", "gimp")},
    }


# ================================================================ backup diário
def backup_agora(forcar=False):
    try:
        feito = auto.backup_diario(forcar)
        if feito:
            log("Backup do dia guardado em backups\\diario\\%s" % os.path.basename(feito))
        return feito
    except Exception as e:
        log("Não consegui fazer o backup do dia: %s" % e)
        return ""


def backup_periodico():
    """Roda ao abrir o painel e de hora em hora (pega a virada do dia com o painel aberto)."""
    while True:
        backup_agora()
        time.sleep(3600)


# ================================================================ ações
def acao(nome, a):
    limpo = bool(a.get("limpo"))
    tabela = {
        "planilha": ("Criar planilhas", lambda: auto.planilha(bool(a.get("refazer")))),
        "ingles": ("Puxar referência em inglês", auto.ingles),
        "aplicar": ("Aplicar traduções", auto.aplicar),
        "repack": ("Gerar ROM", auto.repack),
        "jogar": ("Abrir no melonDS", lambda: auto.jogar(limpo)),
        "tudo": ("Aplicar + gerar + jogar", lambda: (auto.aplicar(), auto.repack() and auto.jogar(limpo))),
        "previa": ("Prévia dos textos", auto.previa),
        "imagens": ("Preparar imagens com texto", lambda: auto.imagens()),
        "galeria": ("Galeria de imagens com texto", auto.galeria),
        "catalogo": ("Catálogo de imagens", auto.catalogo),
        "paletas": ("Achar cores erradas", auto.paletas),
        "reextrair": ("Reextrair imagens", auto.reextrair),
        "replicar": ("Replicar edição", lambda: auto.replicar([a.get("origem", ""), a.get("padrao", "")]
                                                             + (["--forcar"] if a.get("forcar") else []))),
        "lancar": ("Gerar patch de lançamento", auto.lancar),
        "backup": ("Backup das traduções e skins", lambda: backup_agora(True)),
        "modo_ingles": ("Modo inglês", lambda: auto.modo("ingles")),
        "modo_portugues": ("Modo português", lambda: auto.modo("portugues")),
        "status": ("Progresso", auto.status),
    }
    if nome not in tabela:
        return {"ok": False, "msg": "ação desconhecida"}
    titulo, f = tabela[nome]
    if not executar(titulo, f):
        return {"ok": False, "msg": "Espere a tarefa atual terminar: %s" % TAREFA["nome"]}
    return {"ok": True}


# ================================================================ servidor
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, dados, tipo="application/json", codigo=200):
        if not isinstance(dados, (bytes, bytearray)):
            dados = json.dumps(dados, ensure_ascii=False).encode("utf-8")
            tipo = "application/json; charset=utf-8"
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self.responder(PAGINA.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/status":
                return self.responder(status_geral())
            if u.path == "/api/log":
                desde = int(q.get("desde", 0))
                with LOG_LOCK:
                    ultima = LOG[-1] if LOG else ""
                    return self.responder({"linhas": LOG[desde:], "total": len(LOG), "tarefa": TAREFA["nome"],
                                           "ultima": ultima, "versao": LOG_VERSAO[0]})
            if u.path == "/api/textos":
                return self.responder(itens_planilha(q.get("arq", "bin")))
            if u.path == "/api/skin_imagens":
                nome = q.get("nome", "")
                return self.responder([{"chave": k, "src": rel_base(os.path.join(auto.SKINS, nome, k))}
                                       for k in auto.imagens_da_skin(nome)])
            if u.path == "/api/skins":
                return self.responder(auto.listar_skins())
            if u.path == "/api/imagem":
                return self.responder(info_imagem(q.get("tipo", ""), q.get("rel", "")) or {})
            if u.path == "/api/imagens":
                return self.responder(lista_imagens(q.get("filtro", "")))
            if u.path.startswith("/f/"):
                rel = urllib.parse.unquote(u.path[3:])
                p = os.path.normpath(os.path.join(BASE, rel))
                if not p.startswith(BASE) or not os.path.isfile(p):
                    return self.responder({"erro": "não encontrado"}, codigo=404)
                tipo = mimetypes.guess_type(p)[0] or "application/octet-stream"
                with open(p, "rb") as f:
                    return self.responder(f.read(), tipo)
            return self.responder({"erro": "não encontrado"}, codigo=404)
        except Exception as e:
            return self.responder({"erro": str(e)}, codigo=500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            a = self.corpo()
            if u.path == "/api/acao":
                return self.responder(acao(a.get("nome", ""), a))
            if u.path == "/api/texto":
                return self.responder(salvar_texto(a["arq"], int(a["idx"]), a.get("pt", "")))
            if u.path == "/api/previa_texto":
                return self.responder(previa_texto(a.get("arq", "bin"), a.get("pt", "")) or {})
            if u.path == "/api/skin":
                try:
                    if a.get("acao") == "importar":
                        msg = auto.importar_skin(a.get("arquivo", ""))
                    elif a.get("acao") == "variar":
                        nome, novo, graus = a.get("nome", ""), a.get("novo", ""), a.get("graus", 0)
                        if not executar("Variação de cor da skin %s" % nome,
                                        lambda: log("  " + auto.skin("variar", nome=nome, novo=novo, graus=graus))):
                            return self.responder({"ok": False, "msg": "Espere a tarefa atual terminar."})
                        msg = "Gerando a variação... acompanhe no registro."
                    else:
                        msg = auto.skin(a.get("acao", ""), nome=a.get("nome", ""), novo=a.get("novo", ""),
                                        descricao=a.get("descricao", ""), itens=a.get("itens"),
                                        substituir=bool(a.get("substituir")))
                    return self.responder({"ok": True, "msg": msg})
                except Exception as e:
                    return self.responder({"ok": False, "msg": str(e)})
            if u.path == "/api/mod":
                mod, de = copiar_mod(a["tipo"], a["rel"], bool(a.get("original")))
                return self.responder({"ok": True, "mod": mod, "de": de})
            if u.path == "/api/previa_ds":
                return self.responder(previa_ds(a.get("tipo", ""), a.get("rel", ""), a.get("png", ""),
                                                a.get("original", "")))
            if u.path == "/api/limpar":
                if a.get("aplicar"):
                    return self.responder(aplicar_limpeza(a.get("png", ""), a.get("limpa", "")))
                return self.responder(limpar_bordas(a.get("tipo", ""), a.get("rel", ""), a.get("png", ""),
                                                    a.get("original", "")))
            if u.path == "/api/referencia":
                p = referencia(a["tipo"], a["rel"])
                return self.responder({"ok": abrir(p, True), "p": p})
            if u.path == "/api/abrir":
                return self.responder({"ok": abrir(a["p"], bool(a.get("gimp")))})
            if u.path == "/api/config":
                cfg = auto.config(perguntar_melon=False)
                for k in ("melonds", "rom_ingles", "gimp"):
                    if k in a:
                        cfg[k] = a[k].strip().strip('"')
                auto.salvar_config(cfg)
                return self.responder({"ok": True})
            if u.path == "/api/vigia":
                VIGIA["limpo"] = bool(a.get("limpo"))
                if a.get("ligar") and not VIGIA["ligado"]:
                    if not os.path.isfile(auto.config(perguntar_melon=False).get("melonds", "")):
                        return self.responder({"ok": False, "msg": "Configure o melonDS primeiro."})
                    VIGIA["ligado"] = True
                    threading.Thread(target=laco_vigia, daemon=True).start()
                    log("  Modo automático ligado: salve uma planilha ou imagem e o jogo atualiza.")
                elif not a.get("ligar"):
                    VIGIA["ligado"] = False
                return self.responder({"ok": True})
            return self.responder({"erro": "não encontrado"}, codigo=404)
        except Exception as e:
            return self.responder({"ok": False, "erro": str(e)}, codigo=500)


PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Painel Kira Game</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=UnifrakturMaguntia&display=swap" rel="stylesheet">
<style>
/* Visual "caderno": capa preta, páginas de papel escuro, tinta vermelha só no que importa */
:root{
 --fundo:#0f0e0d;--placa:#181614;--placa2:#221f1c;--linha:#2f2b27;--capa:#070606;
 --texto:#ece5d8;--suave:#968d80;--apagado:#6b645b;
 --tinta:#b8262d;--tinta2:#d6434a;--tintaFundo:#2a1517;
 --azul:var(--tinta);--verde:#9cb38a;--ambar:#d9a54a;--erro:#e5534b;
 --raio:6px;--fonte:"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;--mono:"Cascadia Mono",Consolas,monospace;
 --gotica:"UnifrakturMaguntia","Old English Text MT","Goudy Text MT",serif}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--fundo);color:var(--texto);font:14px/1.5 var(--fonte);display:grid;grid-template-columns:236px 1fr;grid-template-rows:1fr auto}
button,input,select,textarea{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--tinta2);outline-offset:2px}
::selection{background:var(--tinta);color:#fff}

/* barra lateral: a capa do caderno */
aside{grid-row:1/3;background:var(--capa);border-right:1px solid #1d1a18;box-shadow:inset -6px 0 10px -8px #000;display:flex;flex-direction:column;padding:22px 12px 14px;gap:2px}
.marca{padding:0 10px 22px}
.marca strong{display:block;font:400 30px/1 var(--gotica);color:var(--texto);letter-spacing:.5px}
.marca span{display:block;color:var(--suave);font-size:12.5px;margin-top:6px}
.nav{display:flex;align-items:center;gap:11px;width:100%;padding:9px 10px;border:0;border-left:2px solid transparent;border-radius:0 6px 6px 0;background:none;color:var(--suave);cursor:pointer;text-align:left}
.nav svg{width:18px;height:18px;flex:none}
.nav:hover{background:#141211;color:var(--texto)}
.nav.on{background:#161413;color:var(--texto);border-left-color:var(--tinta)}
.nav.on svg{color:var(--tinta2)}
.mini{margin-top:auto;padding:14px 10px 4px;border-top:1px solid #1d1a18;font-size:12.5px;color:var(--suave)}
.mini .b{height:3px;background:#1d1a18;margin:5px 0 11px;overflow:hidden}
.mini .b i{display:block;height:100%;background:var(--tinta)}

/* área principal: a página, com a margem vermelha de caderno */
main{overflow:auto;padding:30px 34px 30px 62px;background:linear-gradient(to right,transparent 38px,rgba(184,38,45,.45) 38px,rgba(184,38,45,.45) 39px,transparent 39px) var(--fundo);background-attachment:local}
section{display:none}section.on{display:block}
#inicio,#testar,#exportar,#config{max-width:1180px}
h2{font:400 34px/1.1 var(--gotica);margin:0 0 6px;letter-spacing:.3px}
.sub{color:var(--suave);margin:0 0 22px;max-width:75ch}
.placa{background:var(--placa);border:1px solid var(--linha);border-radius:var(--raio);padding:18px}
.placa h3{font-size:13.5px;margin:0 0 14px;font-weight:600;color:var(--texto);padding-bottom:9px;border-bottom:1px solid var(--linha)}
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--placa2);border:1px solid var(--linha);border-radius:5px;padding:8px 13px;cursor:pointer;font-size:13.5px}
.btn:hover{border-color:#4a443d;background:#29251f}
.btn:disabled{opacity:.45;cursor:default}
.btn.pri{background:var(--tinta);border-color:var(--tinta);color:#f7efe3;font-weight:600}
.btn.pri:hover{background:var(--tinta2);border-color:var(--tinta2)}
.fila{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=text],select,textarea{background:#0b0a09;border:1px solid var(--linha);border-radius:5px;padding:8px 10px}
input[type=text]:focus,textarea:focus,select:focus{border-color:var(--tinta);outline:none}
input[type=checkbox]{accent-color:var(--tinta)}
label.chk{display:inline-flex;gap:6px;align-items:center;color:var(--suave);cursor:pointer}

/* início */
.proximo{display:flex;gap:18px;align-items:center;background:var(--placa);border:1px solid var(--linha);border-left:3px solid var(--tinta);border-radius:var(--raio);padding:18px 22px;margin-bottom:18px}
.proximo .t{flex:1}.proximo .t b{display:block;font-size:16px;margin-bottom:2px}.proximo .t span{color:var(--suave)}
.home{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:16px;align-items:start}
.home .destaque{grid-row:span 3}
.prog{margin-bottom:22px}
.prog .l{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.prog .l span{color:var(--suave);font-size:13px}
.prog .pc{font-size:30px;font-weight:300;font-variant-numeric:tabular-nums;color:var(--texto);line-height:1;margin:6px 0 0}
.prog .b{height:4px;background:var(--placa2);margin-top:10px;overflow:hidden}.prog .b i{display:block;height:100%;background:var(--tinta)}
.notaSkin{border-top:1px solid var(--linha);padding-top:14px;color:var(--suave);display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.notaSkin b{color:var(--texto);font-weight:600}
.passos{list-style:none;margin:0;padding:0}
.passos li{display:flex;gap:12px;align-items:center;padding:9px 0;border-bottom:1px solid var(--linha)}
.passos li:last-child{border:0}
.passos .n{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;font-size:11.5px;border:1px solid var(--linha);color:var(--suave);flex:none}
.passos li.ok .n{border-color:transparent;color:var(--suave)}
.passos li .x{flex:1}.passos li.ok .x{color:var(--apagado)}
#placaPassos .resumo{display:none;color:var(--suave)}
#placaPassos.feito .resumo{display:flex;align-items:center;justify-content:space-between;gap:10px}
#placaPassos.feito .passos{display:none}
#placaPassos.feito.aberto .passos{display:block;margin-top:10px}
.lnk{background:none;border:0;color:var(--tinta2);cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:3px}
.modo{display:inline-flex;flex-wrap:wrap;background:#0b0a09;border:1px solid var(--linha);border-radius:6px;padding:3px;max-width:100%}
.modo button{border:0;background:none;padding:7px 14px;border-radius:4px;cursor:pointer;color:var(--suave)}
.modo button.on{background:var(--placa2);color:var(--texto);box-shadow:inset 0 -2px 0 var(--tinta)}

/* textos */
.tx{display:grid;grid-template-columns:210px minmax(280px,1fr) minmax(360px,1.15fr);gap:14px;height:calc(100vh - 215px)}
.col{display:flex;flex-direction:column;min-height:0}
.rolar{overflow:auto;flex:1;min-height:0}
.cap{display:block;width:100%;text-align:left;border:0;background:none;padding:8px 10px;border-radius:4px;cursor:pointer;color:var(--suave)}
.cap:hover{background:var(--placa2)}.cap.on{background:var(--placa2);color:var(--texto)}
.cap .b{height:3px;background:#0b0a09;margin-top:6px;overflow:hidden}.cap .b i{display:block;height:100%;background:var(--tinta)}
.ln{padding:9px 12px;border-bottom:1px solid var(--linha);border-left:2px solid transparent;cursor:pointer}
.ln:hover{background:var(--placa2)}.ln.on{background:var(--tintaFundo);border-left-color:var(--tinta)}
.ln .jp{font-size:14px}.ln .en{color:var(--suave);font-size:12.5px;font-style:italic}.ln .pt{color:#e3cfa6;font-size:13px}.ln .vz{color:var(--apagado);font-size:12.5px}
.pag{display:flex;gap:6px;align-items:center;justify-content:space-between;padding:8px;border-top:1px solid var(--linha);color:var(--suave);font-size:12.5px}
.ed .jpg{font-size:18px;margin:2px 0 10px}.ed .eng{color:var(--suave);font-style:italic;margin-bottom:12px}
.ed .rot{color:var(--suave);font-size:12.5px;margin:12px 0 5px}
.ed textarea{width:100%;min-height:96px;resize:vertical;font-size:15px}
.dica{color:var(--apagado);font-size:12px;margin-top:6px}
.msgs div{font-size:13px;margin-top:6px}.msgs .e{color:var(--erro)}.msgs .a{color:var(--ambar)}.msgs .s{color:var(--verde)}

/* a tela do DS */
.ds{margin-top:14px;background:#26231f;border-radius:18px;padding:14px;width:max-content;max-width:100%;box-shadow:inset 0 0 0 2px #35302a}
.ds .tela{background:#000;border-radius:4px;padding:10px;width:540px;max-width:100%;min-height:110px;display:flex;align-items:center;justify-content:center}
.ds .tela img{image-rendering:pixelated;width:100%;max-width:520px}
.ds .info{display:flex;justify-content:space-between;color:var(--suave);font-size:12px;margin-top:8px;padding:0 4px}
.ds .info .ruim{color:var(--erro)}

/* imagens */
.pastas{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}
.pasta{border:1px solid var(--linha);background:var(--placa);border-radius:999px;padding:5px 12px;cursor:pointer;color:var(--suave);font-size:13px}
.pasta:hover{color:var(--texto)}
.pasta.on{background:var(--tinta);border-color:var(--tinta);color:#f7efe3}
.im{display:grid;grid-template-columns:minmax(320px,1fr) minmax(380px,1.1fr);gap:14px;height:calc(100vh - 280px)}
.grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:10px;padding:2px}
.th{background:var(--placa2);border:2px solid transparent;border-radius:6px;padding:6px;cursor:pointer;text-align:left;color:var(--texto)}
.th:hover{border-color:#4a443d}.th.on{border-color:var(--tinta)}
.th canvas,.th .vazio{width:100%;height:96px;display:block;border-radius:3px;background:repeating-conic-gradient(#2b2723 0 25%,#221f1b 0 50%) 0 0/10px 10px}
.th .nome{font-size:11.5px;color:var(--suave);margin-top:5px;word-break:break-all;line-height:1.3}
.selos{display:flex;gap:4px;margin-top:4px;flex-wrap:wrap}
.selo{font-size:10.5px;padding:1px 6px;border-radius:3px;background:#302b26;color:#cfc6b8}
.selo.mod{background:var(--tinta);color:#f7efe3}.selo.txt{background:#3a352e;color:#e3cfa6}
.comp{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:12px 0}
.comp figure{margin:0}.comp figcaption{color:var(--suave);font-size:12.5px;margin-bottom:5px}
.comp img{width:100%;image-rendering:pixelated;border-radius:4px;background:repeating-conic-gradient(#2b2723 0 25%,#221f1b 0 50%) 0 0/10px 10px}

.pds{margin-top:16px;background:var(--placa2)}
.resumoDS{margin:0 0 6px;padding-left:18px;color:var(--suave)}.resumoDS li{margin:4px 0}.resumoDS li.ruim{color:var(--texto)}.resumoDS li.ruim b{color:var(--tinta2)}
.pdsImg{position:relative;width:100%;background:repeating-conic-gradient(#2b2723 0 25%,#221f1b 0 50%) 0 0/10px 10px;border-radius:4px}
.pdsImg img{display:block;width:100%;image-rendering:pixelated}
.pdsImg img+img{position:absolute;inset:0;background:none;animation:piscaDS 1.2s steps(2) infinite}
@keyframes piscaDS{50%{opacity:.25}}

/* skins */
.skins{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.sk{background:var(--placa);border:1px solid var(--linha);border-radius:var(--raio);overflow:hidden;display:flex;flex-direction:column}
.sk.ligada{border-color:#4a3a32}.sk.atual{box-shadow:inset 0 0 0 2px var(--tinta)}
.sk .capa{height:132px;background:repeating-conic-gradient(#2b2723 0 25%,#221f1b 0 50%) 0 0/10px 10px}
.sk .capa canvas{width:100%;height:100%;display:block}
.sk .corpo{padding:12px 14px;flex:1;display:flex;flex-direction:column;gap:8px}
.sk .topo{display:flex;align-items:center;gap:10px}.sk .topo b{flex:1;font-size:15px}
.sk .meta{color:var(--suave);font-size:12.5px}
.sk .desc{color:var(--suave);font-size:13px;min-height:18px}
.sk .ordem{display:flex;gap:4px}
.sk .ordem button{width:28px;height:28px;border-radius:4px;border:1px solid var(--linha);background:var(--placa2);cursor:pointer;color:var(--suave)}
.sk .ordem button:disabled{opacity:.3;cursor:default}
.sk .acoesSk{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}
.sk .acoesSk .btn{padding:6px 10px;font-size:12.5px}
.selo.atual{background:var(--tinta);color:#f7efe3}
.dlg{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:5}
.dlg.on{display:flex}
.dlg .caixa{background:var(--placa);border:1px solid var(--linha);border-top:3px solid var(--tinta);border-radius:8px;padding:22px;width:min(460px,92vw)}
.dlg .caixa h3{margin:0 0 12px}.dlg .caixa input{width:100%;margin-bottom:10px}

/* testar e exportar */
.acoes{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.acoes p{color:var(--suave);margin:0 0 12px}
.interruptor{display:flex;align-items:center;gap:12px}
.chave{width:46px;height:26px;border-radius:13px;background:var(--placa2);border:1px solid var(--linha);position:relative;cursor:pointer;flex:none}
.chave::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:var(--suave);transition:left .15s}
.chave.on{background:var(--tinta);border-color:var(--tinta)}.chave.on::after{left:23px;background:#f7efe3}
.cfg label{display:block;margin:14px 0 6px}.cfg input{width:100%}

/* rodapé com o registro */
footer{grid-column:2;border-top:1px solid var(--linha);background:#0b0a09}
.barraLog{display:flex;align-items:center;gap:12px;padding:7px 16px;cursor:pointer;color:var(--suave);font-size:13px}
.barraLog .st{display:inline-flex;align-items:center;gap:7px}
.ponto{width:8px;height:8px;border-radius:50%;background:var(--verde)}
.ocupado .ponto{background:var(--tinta2);animation:pulsa 1s infinite}
@keyframes pulsa{50%{opacity:.35}}
#log{height:0;overflow:auto;margin:0;padding:0 16px;font:12px/1.5 var(--mono);color:#b5ac9f;white-space:pre-wrap;transition:height .15s}
footer.aberto #log{height:190px;padding-bottom:10px}
.aviso{position:fixed;right:20px;bottom:60px;background:var(--placa2);border:1px solid var(--linha);border-left:3px solid var(--tinta);border-radius:6px;padding:11px 14px;max-width:380px;box-shadow:0 10px 28px rgba(0,0,0,.5);display:none}
.aviso.erro{border-left-color:var(--erro)}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media (max-width:1100px){.tx{grid-template-columns:1fr 1fr}.tx .caps{display:none}.im{grid-template-columns:1fr}.home{grid-template-columns:1fr}.home .destaque{grid-row:auto}}
</style></head><body>
<aside>
 <div class="marca"><strong>Kira Game</strong><span id="versao">tradução e mods</span></div>
 <button class="nav on" data-a="inicio"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>Início</button>
 <button class="nav" data-a="textos"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5h16M4 10h16M4 15h10M4 20h7"/></svg>Textos</button>
 <button class="nav" data-a="imagens"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 16l5-5 4 4 3-3 6 6"/></svg>Imagens</button>
 <button class="nav" data-a="skins"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 3l4 3 4-3 5 4-3 4v10H6V11L3 7z"/></svg>Skins</button>
 <button class="nav" data-a="testar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 4l13 8-13 8z"/></svg>Testar no jogo</button>
 <button class="nav" data-a="exportar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12M7 10l5 5 5-5M4 21h16"/></svg>Lançar e pastas</button>
 <button class="nav" data-a="config"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"/></svg>Configurações</button>
 <div class="mini" id="mini"></div>
</aside>

<main>
<section id="inicio" class="on">
 <h2>Início</h2><p class="sub">Onde o projeto está e o que fazer em seguida.</p>
 <div class="proximo" id="proximo"></div>
 <div class="home">
  <div class="placa destaque"><h3>Progresso da tradução</h3><div id="prog"></div></div>
  <div class="placa" id="placaPassos"><h3>Preparação do projeto</h3>
   <div class="resumo"><span>Tudo preparado. Não falta nada.</span><button class="lnk" onclick="placaPassos.classList.toggle('aberto')">Ver etapas</button></div>
   <ol class="passos" id="passos"></ol></div>
  <div class="placa"><h3>Como o jogo é gerado</h3>
   <div class="modo" id="modoSel"><button data-m="ingles">Base em inglês</button><button data-m="portugues">Base em japonês</button></div>
   <p class="sub" style="margin:12px 0 0">É o que aparece no lugar do que você ainda não traduziu. Suas traduções e seus mods entram sempre.</p></div>
  <div class="placa"><h3>Ferramentas</h3><div class="fila">
   <button class="btn" onclick="faz('planilha',{refazer:1})">Atualizar planilhas</button>
   <button class="btn" onclick="faz('paletas')">Achar cores erradas</button>
   <button class="btn" onclick="faz('reextrair')">Reextrair imagens</button></div>
   <p class="sub" style="margin:10px 0 0">Atualizar as planilhas mantém tudo o que você já traduziu.</p></div>
 </div>
</section>

<section id="textos">
 <div class="fila" style="margin-bottom:14px">
  <h2 style="margin:0 12px 0 0">Textos</h2>
  <div class="modo" id="arqSel"><button data-v="bin" class="on">Menus e sistema</button><button data-v="scenario">História</button></div>
  <input type="text" id="busca" placeholder="Buscar em japonês, inglês ou português" style="flex:1;min-width:220px">
  <label class="chk"><input type="checkbox" id="soVazias"> Só sem tradução</label>
 </div>
 <div class="tx">
  <div class="placa col caps" style="padding:8px"><div class="rolar" id="caps"></div></div>
  <div class="placa col" style="padding:0"><div class="rolar" id="linhas"></div><div class="pag" id="pagT"></div></div>
  <div class="placa col ed"><div class="rolar" id="editor"></div></div>
 </div>
</section>

<section id="imagens">
 <div class="fila"><h2 style="margin:0 12px 0 0">Imagens</h2>
  <input type="text" id="buscaI" placeholder="Filtrar pelo nome, ex.: char003" style="flex:1;min-width:200px">
  <label class="chk"><input type="checkbox" id="soTexto"> Com texto</label>
  <label class="chk"><input type="checkbox" id="soMods"> Só as da skin atual</label></div>
 <div id="faixaSkin" class="placa" style="display:none;margin:10px 0 0;padding:10px 14px;border-color:var(--azul)"></div>
 <div class="pastas" id="pastas"></div>
 <div class="im">
  <div class="placa col" style="padding:10px"><div class="rolar"><div class="grade" id="grade"></div></div><div class="pag" id="pagI"></div></div>
  <div class="placa col"><div class="rolar" id="detalhe"><p class="sub">Escolha uma imagem pra comparar o japonês, o inglês e o seu mod, e editar.</p></div></div>
 </div>
</section>

<section id="skins">
 <div class="fila" style="margin-bottom:6px"><h2 style="margin:0;flex:1">Skins</h2>
  <button class="btn" onclick="dlgImportar()">Importar .zip</button>
  <button class="btn pri" onclick="dlgCriar()">Nova skin</button></div>
 <p class="sub">Cada skin é um conjunto de imagens alteradas. Ligue quantas quiser: quando duas mudam a mesma imagem, vale a que está mais acima. Os mods novos entram na skin marcada como "recebe os mods".</p>
 <div class="skins" id="listaSkins"></div>
</section>

<div class="dlg" id="dlg"><div class="caixa" id="dlgCaixa"></div></div>

<section id="testar">
 <h2>Testar no jogo</h2><p class="sub">Gera a ROM com tudo o que você fez e abre no melonDS.</p>
 <div class="acoes">
  <div class="placa"><h3>Jogar agora</h3><p>Aplica as traduções e os mods, gera a ROM e abre o jogo.</p>
   <label class="chk" style="margin-bottom:12px"><input type="checkbox" id="limpo"> Apagar o save antes, pra ver as telas do começo</label><br>
   <div class="fila"><button class="btn pri" onclick="faz('tudo',{limpo:$('#limpo').checked})">Gerar e jogar</button>
   <button class="btn" onclick="faz('jogar',{limpo:$('#limpo').checked})">Só abrir o jogo</button></div></div>
  <div class="placa"><h3>Atualização automática</h3><p>Com isto ligado, cada vez que você salva uma tradução, imagem ou mod o jogo é gerado e reaberto sozinho. Salve no jogo antes, porque ele reinicia.</p>
   <div class="interruptor"><div class="chave" id="chave" role="switch" tabindex="0" onclick="vigia()" onkeydown="if(event.key==' '||event.key=='Enter')vigia()"></div><span id="chaveTxt"></span></div></div>
  <div class="placa"><h3>Conferir antes de jogar</h3><p>Páginas no navegador pra revisar tudo de uma vez.</p>
   <div class="fila"><button class="btn" onclick="faz('previa')">Prévia de todos os textos</button><button class="btn" onclick="faz('galeria')">Imagens com texto</button></div></div>
 </div>
</section>

<section id="exportar">
 <h2>Lançar e pastas</h2><p class="sub">Gera o patch pra compartilhar e abre as pastas do projeto.</p>
 <div class="acoes">
  <div class="placa"><h3>Patch de lançamento</h3><p>Cria um arquivo .xdelta numerado e um LEIAME com instruções e créditos. Gere a ROM antes.</p>
   <div class="fila"><button class="btn pri" onclick="faz('lancar')">Gerar patch</button><button class="btn" onclick="abre('lancamento')">Abrir pasta</button></div></div>
  <div class="placa"><h3>Backup automático</h3><p>Uma vez por dia de uso, o painel guarda num .zip as traduções, as skins, as imagens com texto, o glossário e as configurações. Ficam os 14 dias mais recentes. Pra recuperar, extraia o .zip do dia por cima da pasta do projeto.</p>
   <p id="bkpInfo" style="color:var(--texto)"></p>
   <div class="fila"><button class="btn" onclick="faz('backup')">Fazer backup agora</button><button class="btn" onclick="abre('backups/diario')">Abrir backups</button></div></div>
  <div class="placa"><h3>Pastas do projeto</h3><div class="fila">
   <button class="btn" onclick="abre('traducao')">Planilhas</button><button class="btn" onclick="abre('skins')">Skins</button>
   <button class="btn" onclick="abre('imagens')">Imagens com texto</button><button class="btn" onclick="abre('materiais')">Materiais</button><button class="btn" onclick="abre('backups')">Backups</button>
   <button class="btn" onclick="abre('glossario.txt')">Glossário</button></div></div>
 </div>
</section>

<section id="config">
 <h2>Configurações</h2><p class="sub">No Explorador, segure Shift, clique com o botão direito no arquivo e escolha "Copiar como caminho". Depois cole aqui.</p>
 <div class="placa cfg" style="max-width:720px">
  <label for="c_melonds">melonDS (obrigatório pra jogar)</label><input type="text" id="c_melonds" placeholder="C:\...\melonDS.exe">
  <label for="c_rom_ingles">ROM com o patch em inglês</label><input type="text" id="c_rom_ingles" placeholder="C:\...\Kira Game (EN).nds">
  <label for="c_gimp">GIMP (se ficar vazio, abre no programa padrão de imagens)</label><input type="text" id="c_gimp" placeholder="C:\Program Files\GIMP 3\bin\gimp-3.0.exe">
  <div style="margin-top:16px"><button class="btn pri" onclick="salvaCfg()">Salvar caminhos</button></div>
 </div>
</section>
</main>

<footer id="rodape">
 <div class="barraLog" onclick="rodape.classList.toggle('aberto')"><span class="st" id="st"><span class="ponto"></span><span id="stTxt">Pronto</span></span><span style="margin-left:auto">Registro ▾</span></div>
 <pre id="log"></pre>
</footer>
<div class="aviso" id="aviso"></div>

<script>
const $=s=>document.querySelector(s);const rodape=$('#rodape');
let ST={},TX=[],CAP='',PAGT=0,SEL=null,IM=[],PASTA='',PAGI=0,ISEL=null;const POR_T=80,POR_I=60;
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
async function api(u,d){const r=await fetch(u,d?{method:'POST',body:JSON.stringify(d)}:{});return r.json()}
let avT;function avisa(t,erro){const a=$('#aviso');a.textContent=t;a.className='aviso'+(erro?' erro':'');a.style.display='block';clearTimeout(avT);avT=setTimeout(()=>a.style.display='none',4200)}
async function faz(nome,extra){const r=await api('/api/acao',Object.assign({nome},extra||{}));if(!r.ok)avisa(r.msg||r.erro,true);else{rodape.classList.add('aberto');status()}}
async function abre(p,gimp){const r=await api('/api/abrir',{p,gimp:!!gimp});if(!r.ok)avisa('Ainda não existe: '+p,true)}
function vai(a){history.replaceState(null,'','#'+a);document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('on',b.dataset.a==a));document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id==a));
 if(a=='textos'&&!TX.length)carregaTextos();if(a=='imagens'&&!IM.length)carregaImagens();if(a=='skins')carregaSkins()}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>vai(b.dataset.a));

// ---------- status e início
const PASSOS=[['Planilhas de tradução','Criar planilhas','planilha'],['Referência em inglês','Puxar o inglês','ingles'],['Imagens com texto preparadas','Preparar imagens','imagens'],['melonDS configurado','Configurar','#config'],['ROM gerada (dn1_patched.nds)','Gerar e jogar','tudo']];
const NOMES={'Planilhas de tradução':'Criar as planilhas de tradução','Referência em inglês':'Puxar o texto em inglês como referência','Imagens com texto preparadas':'Separar as imagens que têm texto','melonDS configurado':'Dizer onde está o melonDS','ROM gerada (dn1_patched.nds)':'Gerar a ROM e testar'};
function pct(a,b){return b?Math.round(100*a/b):0}
async function status(){ST=await api('/api/status');
 const ocup=!!ST.tarefa;$('#st').className='st'+(ocup?' ocupado':'');$('#stTxt').textContent=ocup?ST.tarefa+'…':'Pronto';
 const P=ST.progresso,I=ST.imagens;let h='',mini='';
 for(const[k,n]of[['bin','Menus e sistema'],['scenario','História']]){const v=P[k];if(!v)continue;const p=pct(v.feitas,v.total);
  h+=`<div class="prog"><div class="l"><b>${n}</b><span>${v.feitas} de ${v.total} frases</span></div><div class="pc">${p}%</div><div class="b"><i style="width:${p}%"></i></div></div>`;
  mini+=`${n}<div class="b"><i style="width:${p}%"></i></div>`}
 if(I.total){const p=pct(I.editadas,I.total);h+=`<div class="prog"><div class="l"><b>Imagens com texto</b><span>${I.editadas} de ${I.total} imagens</span></div><div class="pc">${p}%</div><div class="b"><i style="width:${p}%"></i></div></div>`;mini+=`Imagens<div class="b"><i style="width:${p}%"></i></div>`}
 h+=`<div class="notaSkin"><span style="flex:1">Skin que recebe os mods: <b>${esc(ST.skin_atual)}</b>, com ${ST.mods} ${ST.mods==1?'imagem':'imagens'}. ${ST.skins_ligadas} ${ST.skins_ligadas==1?'skin ligada':'skins ligadas'}.</span><button class="btn" onclick="vai('skins')">Abrir skins</button></div>`;$('#prog').innerHTML=h;$('#mini').innerHTML=(mini||'Crie as planilhas pra ver o progresso.')+`<div style="margin-top:4px">Skins ligadas: ${ST.skins_ligadas}<br>Mods novos vão pra: <b style="color:var(--texto)">${esc(ST.skin_atual)}</b></div>`;
 $('#passos').innerHTML=PASSOS.map(([k,b,a])=>{const ok=ST.checar[k];return `<li class="${ok?'ok':''}"><span class="n">${ok?'✓':PASSOS.findIndex(p=>p[0]==k)+1}</span><span class="x">${NOMES[k]}</span>${ok?'':`<button class="btn" onclick="passo('${a}')">${b}</button>`}</li>`}).join('');
 const falta=PASSOS.find(([k])=>!ST.checar[k]);$('#placaPassos').classList.toggle('feito',!falta);
 $('#proximo').innerHTML=falta?`<div class="t"><b>Próximo passo: ${NOMES[falta[0]]}</b><span>${dicaPasso(falta[2])}</span></div><button class="btn pri" onclick="passo('${falta[2]}')">${falta[1]}</button>`
  :`<div class="t"><b>Tudo pronto pra trabalhar</b><span>Traduza na aba Textos, edite na aba Imagens e teste quando quiser.</span></div><button class="btn pri" onclick="vai('textos')">Abrir textos</button>`;
 $('#versao').textContent='tradução e mods · versão '+ST.versao;
 $('#bkpInfo').textContent=ST.ultimo_backup?'Último backup: '+ST.ultimo_backup.split('-').reverse().join('/')+(ST.ultimo_backup==new Date().toLocaleDateString('sv')?' (hoje)':''):'Ainda não há backup.';
 document.querySelectorAll('#modoSel button').forEach(b=>b.classList.toggle('on',(b.dataset.m=='ingles')==ST.modo_ingles));
 $('#chave').classList.toggle('on',ST.vigia);$('#chave').setAttribute('aria-checked',ST.vigia);$('#chaveTxt').textContent=ST.vigia?'Ligada':'Desligada';
 for(const k of['melonds','rom_ingles','gimp']){const e=$('#c_'+k);if(document.activeElement!==e&&!e.dataset.mexido)e.value=ST.config[k]||''}}
function dicaPasso(a){return{planilha:'Separa as frases do jogo em arquivos fáceis de traduzir.',ingles:'Precisa do caminho da ROM em inglês nas Configurações.',imagens:'Compara com o patch em inglês e separa as imagens que têm texto.','#config':'Sem isso não dá pra abrir o jogo direto daqui.',tudo:'Aplica tudo e abre o jogo no melonDS.'}[a]}
function passo(a){if(a=='#config')vai('config');else if(a=='ingles'&&!ST.config.rom_ingles){vai('config');avisa('Cole o caminho da ROM em inglês e salve.')}else faz(a)}
document.querySelectorAll('#modoSel button').forEach(b=>b.onclick=()=>faz(b.dataset.m=='ingles'?'modo_ingles':'modo_portugues'));
async function salvaCfg(){await api('/api/config',{melonds:$('#c_melonds').value,rom_ingles:$('#c_rom_ingles').value,gimp:$('#c_gimp').value});document.querySelectorAll('.cfg input').forEach(e=>delete e.dataset.mexido);status();avisa('Caminhos salvos.')}
document.querySelectorAll('.cfg input').forEach(e=>e.oninput=()=>e.dataset.mexido=1);
async function vigia(){const r=await api('/api/vigia',{ligar:!ST.vigia,limpo:$('#limpo').checked});if(!r.ok)avisa(r.msg,true);status()}

// ---------- registro
let logN=0,logV=0,LINHAS=[];
async function logs(){const r=await api('/api/log?desde='+logN);const L=$('#log');const fim=L.scrollTop+L.clientHeight>=L.scrollHeight-20;let m=false;
 if(r.versao!==logV&&LINHAS.length&&!r.linhas.length){LINHAS[LINHAS.length-1]=r.ultima;m=true}
 if(r.linhas.length){LINHAS.push(...r.linhas);logN=r.total;m=true}logV=r.versao;
 if(m){if(LINHAS.length>3000)LINHAS=LINHAS.slice(-2000);L.textContent=LINHAS.join('\n');if(fim)L.scrollTop=L.scrollHeight}
 if((r.tarefa||'')!==(ST.tarefa||''))status()}
setInterval(logs,700);setInterval(status,5000);status();
if(location.hash.length>1)setTimeout(()=>vai(location.hash.slice(1)),50);

// ---------- textos
document.querySelectorAll('#arqSel button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#arqSel button').forEach(x=>x.classList.toggle('on',x==b));carregaTextos()});
const arq=()=>$('#arqSel .on').dataset.v;
async function carregaTextos(){TX=await api('/api/textos?arq='+arq());CAP='';PAGT=0;SEL=null;desenhaTextos();
 $('#editor').innerHTML=TX.length?'<p class="sub">Escolha uma frase na lista do meio.</p>':'<p class="sub">Ainda não há planilhas. Crie na aba Início.</p>'}
function filtroT(){const f=$('#busca').value.toLowerCase(),v=$('#soVazias').checked;return TX.filter(t=>(!CAP||t.secao==CAP)&&(!v||!t.pt)&&(!f||(t.jp+'\n'+t.en+'\n'+t.pt).toLowerCase().includes(f)))}
$('#busca').oninput=()=>{PAGT=0;desenhaTextos()};$('#soVazias').onchange=()=>{PAGT=0;desenhaTextos()};
function desenhaTextos(){
 const caps={};for(const t of TX){const c=caps[t.secao]||(caps[t.secao]={n:0,f:0});c.n++;if(t.pt)c.f++}
 const tot=TX.length,fe=TX.filter(t=>t.pt).length;
 let h=`<button class="cap${CAP==''?' on':''}" onclick="CAP='';PAGT=0;desenhaTextos()">Tudo · ${fe}/${tot}<div class="b"><i style="width:${pct(fe,tot)}%"></i></div></button>`;
 for(const[c,v]of Object.entries(caps)){if(!c)continue;h+=`<button class="cap${CAP==c?' on':''}" onclick='CAP=${JSON.stringify(c)};PAGT=0;desenhaTextos()'>${esc(c.replace('.bin',''))} · ${v.f}/${v.n}<div class="b"><i style="width:${pct(v.f,v.n)}%"></i></div></button>`}
 $('#caps').innerHTML=h;
 const L=filtroT(),pags=Math.max(1,Math.ceil(L.length/POR_T));PAGT=Math.min(PAGT,pags-1);
 $('#linhas').innerHTML=L.slice(PAGT*POR_T,(PAGT+1)*POR_T).map(t=>`<div class="ln${SEL&&SEL.idx==t.idx?' on':''}" onclick="edita(${t.idx})"><div class="jp">${esc(t.jp)}</div>${t.en?`<div class="en">${esc(t.en)}</div>`:''}<div class="${t.pt?'pt':'vz'}">${t.pt?esc(t.pt):'Sem tradução'}</div></div>`).join('')||'<p class="sub" style="padding:12px">Nenhuma frase com esse filtro.</p>';
 $('#pagT').innerHTML=`<button class="btn" ${PAGT==0?'disabled':''} onclick="PAGT--;desenhaTextos();$('#linhas').scrollTop=0">Anterior</button><span>${L.length} frases · página ${PAGT+1} de ${pags}</span><button class="btn" ${PAGT>=pags-1?'disabled':''} onclick="PAGT++;desenhaTextos();$('#linhas').scrollTop=0">Próxima</button>`}
function edita(idx){SEL=TX.find(t=>t.idx==idx);desenhaTextos();
 $('#editor').innerHTML=`<div class="sub" style="margin:0 0 8px">${esc((SEL.secao||'').replace('.bin',''))}, linha ${SEL.idx+1} da planilha</div>
 <div class="jpg">${esc(SEL.jp)}</div>${SEL.en?`<div class="eng">${esc(SEL.en)}</div>`:''}
 <div class="rot">Sua tradução</div><textarea id="pt" spellcheck="true" lang="pt-BR">${esc(SEL.pt)}</textarea>
 <div class="dica">Ctrl+Enter salva e vai pra próxima sem tradução. Enter ou | quebram a linha, &lt;&lt; no começo centraliza, {{ no começo mantém as quebras que você pôs, ! deixa a frase vazia.</div>
 <div class="fila" style="margin-top:10px"><button class="btn pri" onclick="salva(true)">Salvar e ir pra próxima</button><button class="btn" onclick="salva(false)">Salvar</button>${SEL.en?`<button class="btn" onclick="$('#pt').value=SEL.en.replace(/ \\/ /g,'|');previa()">Usar o inglês como base</button>`:''}</div>
 <div class="msgs" id="msgs"></div>
 <div class="ds"><div class="tela" id="tela"></div><div class="info" id="telaInfo"></div></div>`;
 const ta=$('#pt');ta.focus();ta.oninput=previa;ta.onkeydown=e=>{if(e.key=='Enter'&&e.ctrlKey){e.preventDefault();salva(true)}};previa()}
let pvT;function previa(){clearTimeout(pvT);pvT=setTimeout(async()=>{const v=$('#pt').value.replace(/\n/g,'|'),pt=v||SEL.en.replace(/ \/ /g,'|');
 if(!pt){$('#tela').innerHTML='';$('#telaInfo').innerHTML='';return}
 const r=await api('/api/previa_texto',{arq:arq(),pt});if(!r.png){$('#tela').innerHTML='';return}
 $('#tela').innerHTML=`<img alt="Prévia da frase com a fonte do jogo" src="data:image/png;base64,${r.png}">`;
 $('#telaInfo').innerHTML=`<span>${v?'Como vai aparecer no jogo':'Mostrando o inglês (você ainda não escreveu)'}</span><span class="${r.ruim?'ruim':''}">${r.largura} de ${r.limite} pixels · ${r.linhas} linha(s)${r.ruim?' · não cabe':''}</span>`},220)}
async function salva(prox){const pt=$('#pt').value;const r=await api('/api/texto',{arq:arq(),idx:SEL.idx,pt});
 if(!r.ok){$('#msgs').innerHTML=`<div class="e">${esc((r.erros||[r.erro]).join('. '))}</div>`;return}
 SEL.pt=pt.replace(/\r/g,'').replace(/\n/g,'|');
 const m=r.erros.map(e=>`<div class="e">Não vai pro jogo até corrigir: ${esc(e)}</div>`).join('')+r.avisos.map(a=>`<div class="a">${esc(a)}</div>`).join('');
 if(prox&&!r.erros.length){const L=filtroT();const i=L.findIndex(t=>t.idx==SEL.idx);const nx=L.slice(i+1).find(t=>!t.pt);
  if(nx){const p=Math.floor(L.indexOf(nx)/POR_T);PAGT=p;edita(nx.idx);if(m)$('#msgs').innerHTML='<div class="sub">Na frase anterior:</div>'+m;return}}
 $('#msgs').innerHTML=m||'<div class="s">Salvo.</div>';desenhaTextos()}

// ---------- skins
let SK=[];
async function carregaSkins(){SK=await api('/api/skins');const L=SK.filter(s=>s.ativa).length;
 $('#listaSkins').innerHTML=SK.map(s=>`<article class="sk${s.ativa?' ligada':''}${s.atual?' atual':''}">
 <div class="capa">${s.capa?`<canvas data-src="/f/${encodeURI(s.capa)}"></canvas>`:''}</div>
 <div class="corpo"><div class="topo"><b>${esc(s.nome)}</b>
  <div class="chave${s.ativa?' on':''}" role="switch" tabindex="0" aria-checked="${s.ativa}" title="${s.ativa?'Desligar':'Ligar'}" onclick='opSkin("${s.ativa?'desligar':'ligar'}",${JSON.stringify(s.nome)})'></div></div>
  <div class="meta">${s.imagens} imagem(ns)${s.ativa?` · posição ${s.posicao} de ${L}`:' · desligada'}</div>
  ${s.atual?'<div><span class="selo atual">recebe os mods novos</span></div>':''}
  <div class="desc">${esc(s.descricao)||'Sem descrição.'}</div>
  <div class="acoesSk">
   ${s.ativa?`<span class="ordem"><button title="Subir (vence as de baixo)" ${s.posicao==1?'disabled':''} onclick='opSkin("subir",${JSON.stringify(s.nome)})'>▲</button><button title="Descer" ${s.posicao==L?'disabled':''} onclick='opSkin("descer",${JSON.stringify(s.nome)})'>▼</button></span>`:''}
   ${s.atual?'':`<button class="btn" onclick='opSkin("atual",${JSON.stringify(s.nome)})'>Receber os mods</button>`}
   <button class="btn" onclick='verSkin(${JSON.stringify(s.nome)})'>Ver imagens</button>
   ${SK.length>1?`<button class="btn" onclick='dlgTrazer(${JSON.stringify(s.nome)})'>Trazer de outra skin</button>`:''}
   <button class="btn" onclick='dlgEditar(${JSON.stringify(s.nome)})'>Editar</button>
   <button class="btn" onclick='dlgVariar(${JSON.stringify(s.nome)})'>Variar cores</button>
   <button class="btn" onclick='opSkin("duplicar",${JSON.stringify(s.nome)})'>Duplicar</button>
   <button class="btn" onclick='opSkin("exportar",${JSON.stringify(s.nome)})'>Exportar</button>
   <button class="btn" onclick='abre(${JSON.stringify(s.pasta)})'>Pasta</button>
   <button class="btn" onclick='dlgApagar(${JSON.stringify(s.nome)})'>Apagar</button></div></div></article>`).join('');
 document.querySelectorAll('#listaSkins canvas').forEach(c=>OBS.observe(c))}
async function opSkin(acao,nome,extra){const r=await api('/api/skin',Object.assign({acao,nome},extra||{}));avisa(r.msg,!r.ok);if(r.ok){fechaDlg();carregaSkins();status();IM=[]}return r.ok}
function abreDlg(h){$('#dlgCaixa').innerHTML=h;$('#dlg').classList.add('on');const i=$('#dlgCaixa input');if(i)i.focus()}
function fechaDlg(){$('#dlg').classList.remove('on')}
$('#dlg').onclick=e=>{if(e.target.id=='dlg')fechaDlg()};
function dlgCriar(){abreDlg(`<h3>Nova skin</h3><label class="sub">Nome</label><input type="text" id="dNome" placeholder="Ex.: Light de terno">
 <label class="sub">Descrição (opcional)</label><input type="text" id="dDesc" placeholder="O que essa skin muda">
 <div class="fila"><button class="btn pri" onclick="opSkin('criar','',{novo:$('#dNome').value,descricao:$('#dDesc').value})">Criar skin</button><button class="btn" onclick="fechaDlg()">Cancelar</button></div>`)}
function dlgEditar(n){const s=SK.find(x=>x.nome==n);abreDlg(`<h3>Editar skin</h3><label class="sub">Nome</label><input type="text" id="dNome" value="${esc(s.nome)}">
 <label class="sub">Descrição</label><input type="text" id="dDesc" value="${esc(s.descricao)}">
 <div class="fila"><button class="btn pri" onclick='opSkin("renomear",${JSON.stringify(n)},{novo:$("#dNome").value,descricao:$("#dDesc").value})'>Salvar</button><button class="btn" onclick="fechaDlg()">Cancelar</button></div>`)}
function dlgVariar(n){abreDlg(`<h3>Variar as cores de ${esc(n)}</h3><p class="sub">Cria uma skin nova com todas as imagens desta girando o tom das cores. 180 inverte as cores, 30 a 90 muda pra tons vizinhos.</p>
 <label class="sub">Quanto girar (graus, de -180 a 180)</label><input type="text" id="dGraus" value="120">
 <div class="fila"><button class="btn pri" onclick='opSkin("variar",${JSON.stringify(n)},{graus:parseInt($("#dGraus").value)||0}).then(()=>setTimeout(carregaSkins,4000))'>Criar variação</button><button class="btn" onclick="fechaDlg()">Cancelar</button></div>`)}
async function dlgTrazer(dest,orig){const outras=SK.filter(x=>x.nome!=dest);orig=orig||(outras.find(x=>x.imagens)||outras[0]).nome;
 abreDlg(`<h3>Trazer imagens pra ${esc(dest)}</h3><p class="sub">Escolha de qual skin copiar e marque as imagens. As originais continuam na skin de origem.</p>
 <select id="dOrig" style="width:100%;margin-bottom:10px" onchange='dlgTrazer(${JSON.stringify(dest)},this.value)'>${outras.map(x=>`<option ${x.nome==orig?'selected':''} value="${esc(x.nome)}">${esc(x.nome)} (${x.imagens})</option>`).join('')}</select>
 <div class="fila" style="margin-bottom:8px"><label class="chk"><input type="checkbox" id="dTodas" onchange="document.querySelectorAll('.dIt').forEach(c=>c.checked=this.checked)"> Marcar todas</label>
 <label class="chk"><input type="checkbox" id="dSubst" onchange="gradeTrazer()"> Também trocar as que já estão na skin</label></div>
 <div class="grade" id="dGrade" style="max-height:46vh;overflow:auto;grid-template-columns:repeat(auto-fill,minmax(110px,1fr))"><p class="sub">Carregando…</p></div>
 <div class="fila" style="margin-top:12px"><button class="btn pri" onclick='trazer(${JSON.stringify(dest)})'>Copiar marcadas</button><button class="btn" onclick="fechaDlg()">Cancelar</button></div>`);
 $('#dlgCaixa').style.width='min(760px,94vw)';
 const L=await api('/api/skin_imagens?nome='+encodeURIComponent(orig));
 const ja=new Set((await api('/api/skin_imagens?nome='+encodeURIComponent(dest))).map(x=>x.chave));
 TRZ={L,ja};gradeTrazer()}
let TRZ={L:[],ja:new Set()};
function gradeTrazer(){const sub=$('#dSubst').checked,{L,ja}=TRZ;
 $('#dGrade').innerHTML=L.map(i=>{const tem=ja.has(i.chave);return `<label class="th" style="cursor:pointer${tem&&!sub?';opacity:.55':''}"><canvas data-src="/f/${encodeURI(i.src)}"></canvas><div class="nome">${tem&&!sub?'':`<input type="checkbox" class="dIt" value="${esc(i.chave)}">`} ${esc(i.chave.split('/').pop())}</div>${tem?'<div class="selos"><span class="selo mod">já está na skin</span></div>':''}</label>`}).join('')||'<p class="sub">Essa skin não tem imagens.</p>';
 document.querySelectorAll('#dGrade canvas').forEach(c=>OBS.observe(c))}
async function trazer(dest){const itens=[...document.querySelectorAll('.dIt:checked')].map(c=>c.value);if(!itens.length){avisa('Marque pelo menos uma imagem.',true);return}
 await opSkin('copiar',$('#dOrig').value,{novo:dest,itens,substituir:$('#dSubst').checked});$('#dlgCaixa').style.width=''}
function dlgApagar(n){abreDlg(`<h3>Apagar a skin ${esc(n)}?</h3><p class="sub">Ela sai da lista e do jogo. Uma cópia fica guardada em backups\\skins_apagadas, se você mudar de ideia.</p>
 <div class="fila"><button class="btn pri" style="background:var(--erro);border-color:var(--erro)" onclick='opSkin("apagar",${JSON.stringify(n)})'>Apagar skin</button><button class="btn" onclick="fechaDlg()">Cancelar</button></div>`)}
function dlgImportar(){abreDlg(`<h3>Importar skin</h3><p class="sub">Cole o caminho do arquivo .zip de uma skin exportada (no Explorador: Shift + botão direito, Copiar como caminho).</p>
 <input type="text" id="dZip" placeholder="C:\\...\\minha skin.zip">
 <div class="fila"><button class="btn pri" onclick="opSkin('importar','',{arquivo:$('#dZip').value})">Importar</button><button class="btn" onclick="fechaDlg()">Cancelar</button></div>`)}
async function verSkin(n){const s=SK.find(x=>x.nome==n);if(!s.atual){await opSkin('atual',n)}vai('imagens');if(!IM.length)await carregaImagens();$('#soMods').checked=true;PAGI=0;desenhaImagens()}

// ---------- imagens
async function carregaImagens(){$('#grade').innerHTML='<p class="sub">Carregando…</p>';IM=await api('/api/imagens?filtro=');PAGI=0;desenhaPastas();desenhaImagens()}
const pastaDe=i=>i.tipo=='NSBMD'?'3D / '+i.rel.split('/')[0]:i.rel.split('/')[0];
function desenhaPastas(){const c={};for(const i of IM){const p=pastaDe(i);c[p]=(c[p]||0)+1}
 $('#pastas').innerHTML=`<button class="pasta${PASTA==''?' on':''}" onclick="PASTA='';PAGI=0;desenhaPastas();desenhaImagens()">Todas · ${IM.length}</button>`+Object.entries(c).sort().map(([p,n])=>`<button class="pasta${PASTA==p?' on':''}" onclick='PASTA=${JSON.stringify(p)};PAGI=0;desenhaPastas();desenhaImagens()'>${esc(p)} · ${n}</button>`).join('')}
function filtroI(){const f=$('#buscaI').value.toLowerCase(),t=$('#soTexto').checked,m=$('#soMods').checked;return IM.filter(i=>(!PASTA||pastaDe(i)==PASTA)&&(!t||i.texto)&&(!m||i.mod)&&(!f||(i.tipo+'/'+i.rel).toLowerCase().includes(f)))}
$('#buscaI').oninput=()=>{PAGI=0;desenhaImagens()};$('#soTexto').onchange=$('#soMods').onchange=()=>{PAGI=0;desenhaImagens()};
function desenhaImagens(){const fx=$('#faixaSkin');
 if($('#soMods').checked){fx.style.display='flex';fx.style.alignItems='center';fx.style.gap='12px';
  fx.innerHTML=`<span style="flex:1">Mostrando só as imagens que já estão na skin <b>${esc(ST.skin_atual)}</b>. Pra adicionar outras, veja todas e escolha a imagem.</span><button class="btn pri" onclick="$('#soMods').checked=false;PAGI=0;desenhaImagens()">Ver todas as imagens</button>`}
 else{fx.style.display='none'}
 const L=filtroI(),pags=Math.max(1,Math.ceil(L.length/POR_I));PAGI=Math.min(PAGI,pags-1);
 $('#grade').innerHTML=L.slice(PAGI*POR_I,(PAGI+1)*POR_I).map(i=>{const k=i.tipo+'/'+i.rel;return `<button class="th${ISEL&&ISEL.tipo+'/'+ISEL.rel==k?' on':''}" onclick='mostra(${JSON.stringify(k)})' title="${esc(k)}"><canvas data-src="/f/${encodeURI(i.mod||i.outra_src||i.en||i.jp)}"></canvas><div class="nome">${esc(i.rel.split('/').pop())}</div><div class="selos">${i.mod?'<span class="selo mod">skin</span>':''}${(i.outras||[]).length&&!i.mod?'<span class="selo">modificada em '+esc(i.outras[0])+'</span>':''}${i.texto?'<span class="selo txt">texto</span>':''}${i.trad_editada?'<span class="selo">traduzida</span>':''}</div></button>`}).join('')||vazioImagens();
 $('#pagI').innerHTML=`<button class="btn" ${PAGI==0?'disabled':''} onclick="PAGI--;desenhaImagens()">Anterior</button><span>${L.length} imagens · página ${PAGI+1} de ${pags}</span><button class="btn" ${PAGI>=pags-1?'disabled':''} onclick="PAGI++;desenhaImagens()">Próxima</button>`;
 document.querySelectorAll('#grade canvas').forEach(c=>OBS.observe(c));$('#grade').parentElement.scrollTop=0}
function vazioImagens(){if($('#soMods').checked)return `<div style="grid-column:1/-1;padding:6px"><p><b>A skin ${esc(ST.skin_atual)} ainda não tem imagens${PASTA?' nesta pasta':''}.</b></p><p class="sub">O filtro "Na skin atual" mostra só o que já foi adicionado a ela. Desmarque pra ver todas as imagens do jogo, escolha uma e clique em "Adicionar à skin".</p><button class="btn pri" onclick="$('#soMods').checked=false;PAGI=0;desenhaImagens()">Mostrar todas as imagens</button></div>`;
 return '<p class="sub">Nenhuma imagem com esse filtro. Tente outra pasta ou apague a busca.</p>'}
// miniatura: tira a faixa de paleta da direita e as bordas transparentes
const OBS=new IntersectionObserver(es=>es.forEach(e=>{if(!e.isIntersecting)return;OBS.unobserve(e.target);miniatura(e.target)}),{rootMargin:'200px'});
function miniatura(cv){const img=new Image();img.onload=()=>{let w=img.width,h=img.height;if(w>64)w-=40;
 const t=document.createElement('canvas');t.width=w;t.height=h;const x=t.getContext('2d');x.drawImage(img,0,0);
 let d;try{d=x.getImageData(0,0,w,h).data}catch(e){return}let a=w,b=h,c=-1,f=-1;
 for(let y=0;y<h;y++)for(let i=0;i<w;i++)if(d[(y*w+i)*4+3]>0){if(i<a)a=i;if(i>c)c=i;if(y<b)b=y;if(y>f)f=y}
 if(c<0){a=0;b=0;c=w-1;f=h-1}const cw=c-a+1,ch=f-b+1;cv.width=cv.clientWidth*2;cv.height=cv.clientHeight*2;const g=cv.getContext('2d');g.imageSmoothingEnabled=false;
 const s=Math.min(cv.width/cw,cv.height/ch);g.drawImage(t,a,b,cw,ch,(cv.width-cw*s)/2,(cv.height-ch*s)/2,cw*s,ch*s)};img.src=cv.dataset.src}
async function mostra(k){ISEL=IM.find(i=>i.tipo+'/'+i.rel==k);
 const novo=await api('/api/imagem?tipo='+encodeURIComponent(ISEL.tipo)+'&rel='+encodeURIComponent(ISEL.rel));
 if(novo&&novo.rel){Object.assign(ISEL,novo)}const y=$('#grade').parentElement.scrollTop;desenhaImagens();$('#grade').parentElement.scrollTop=y;const i=ISEL,ts=Date.now();
 const fig=(t,p)=>p?`<figure><figcaption>${t}</figcaption><img alt="${t}" src="/f/${encodeURI(p)}?${ts}"></figure>`:'';
 const nome=i.rel.split('/').pop().replace('.png','');
 $('#detalhe').innerHTML=`<h3 style="margin:0 0 2px">${esc(nome)}</h3><div class="sub" style="margin:0">${esc(i.tipo+'/'+i.rel)}</div>
 <div class="comp">${fig('Japonês',i.jp)}${fig('Inglês',i.en)}${i.trad?fig(i.trad_editada?'Sua tradução':'Tradução (ainda igual ao inglês)',i.trad):''}${fig('Na skin '+(ST.skin_atual||''),i.mod)}</div>${(i.outras||[]).length&&i.mod&&i.outras.length?`<p class="sub" style="margin:0 0 8px">Também existe versão modificada em: ${i.outras.map(esc).join(', ')}.</p><div class="fila" style="margin-bottom:10px">${i.outras.map(o=>`<button class="btn" onclick='trazUma(${JSON.stringify(o)})'>Trocar pela versão de ${esc(o)}</button>`).join('')}</div>`:''}
 <div class="fila">${i.mod?`<button class="btn pri" onclick="editaMod()">Editar o mod</button>`:((i.outras||[]).length?`<button class="btn pri" onclick="criaMod()">Adicionar a versão modificada (de ${esc(i.outras[0])})</button>`
   +(i.outras.length>1?i.outras.slice(1).map(o=>`<button class="btn" onclick='trazUma(${JSON.stringify(o)})'>Usar a de ${esc(o)}</button>`).join(''):'')
   +`<button class="btn" onclick="criaMod(true)" title="Ignora as versões modificadas">Começar do original</button>`
  :`<button class="btn pri" onclick="criaMod()">Adicionar à skin ${esc(ST.skin_atual||'')}</button>`)}
 ${i.trad?`<button class="btn" onclick="abre('${i.trad}',true)">Editar a tradução</button>`:''}
 <button class="btn" onclick="abre('${(i.mod||i.trad||i.jp).replace(/\/[^\/]*$/,'')}')">Abrir a pasta</button>
 <button class="btn" onclick="usaRef()" title="Abre uma cópia no GIMP, fora das skins. Bom pra recortar roupas e partes.">Usar como referência</button>
 ${i.mod&&i.tipo=='IMG'?`<button class="btn" onclick="previaDS()" title="Mostra como o mod vai ficar no jogo, sem gerar a ROM">Prévia no DS</button>`:''}</div>
 <div id="previaDS"></div>
 ${i.mod?`<div class="placa" style="margin-top:16px;background:var(--placa2)"><h3>Levar as mudanças de cor pra outras imagens</h3>
 <p class="sub" style="margin:0 0 10px">Aplica as mesmas trocas de cor deste mod em todas as imagens cujo nome contém o texto abaixo.</p>
 <div class="fila"><input type="text" id="padrao" value="${esc(nome.replace(/[ud]$/,''))}"><label class="chk"><input type="checkbox" id="forcar"> Substituir mods que já existem</label>
 <button class="btn" onclick="faz('replicar',{origem:'${esc(nome)}',padrao:$('#padrao').value,forcar:$('#forcar').checked})">Aplicar</button></div></div>`:''}`}
async function previaDS(){const i=ISEL,box=$('#previaDS');
 box.innerHTML='<div class="placa pds"><p class="sub" style="margin:0">Simulando as cores do DS…</p></div>';
 const r=await api('/api/previa_ds',{tipo:i.tipo,rel:i.rel,png:i.mod,original:(ST.modo_ingles&&i.en)?i.en:i.jp});
 if(ISEL!==i)return;
 if(!r.ok){box.innerHTML=`<div class="placa pds"><p class="sub" style="margin:0">${esc(r.msg||'Não consegui gerar a prévia.')}</p></div>`;return}
 const ts=Date.now(),lin=[];
 lin.push(r.buracos?`<li class="ruim"><b>${r.buracos} ${r.buracos==1?'pixel vira buraco':'pixels viram buracos'}</b> (em azul): estão com a cor da transparência, e o cenário vai aparecer através deles. Pinte com outra cor escura.</li>`:'<li>Nenhum buraco: nada do desenho vira transparente.</li>');
 lin.push(r.mudaram?`<li class="${r.mudaram>200?'ruim':''}"><b>${r.mudaram} ${r.mudaram==1?'pixel muda':'pixels mudam'} bastante de cor</b> (em vermelho): a paleta não tem uma cor parecida. Use menos tons ou cores que já existem na imagem.</li>`:'<li>As cores ficam praticamente iguais às que você pintou.</li>');
 if(r.cores_novas)lin.push(`<li>${r.cores_novas} ${r.cores_novas==1?'cor nova entra':'cores novas entram'} na paleta deste retrato.</li>`);
 (r.avisos||[]).filter(a=>a.startsWith('sem espaço')).forEach(a=>lin.push(`<li class="ruim">${esc(a)}.</li>`));
 box.innerHTML=`<div class="placa pds"><h3>Prévia no DS</h3><ul class="resumoDS">${lin.join('')}</ul>
  <label class="chk" style="margin:4px 0 10px"><input type="checkbox" id="vMarcas" checked onchange="$('#pdsM').style.display=this.checked?'':'none'"> Marcar os problemas</label>
  <div class="pdsImg"><img alt="Como fica no jogo" src="/f/${encodeURI(r.ds)}?${ts}"><img id="pdsM" alt="" src="/f/${encodeURI(r.marcas)}?${ts}"></div>
  <p class="dica">Simulação feita com a paleta do jogo e as cores novas que o painel coloca nela. O teste final continua sendo gerar a ROM.</p>
  ${(r.buracos||r.mudaram)?'<div class="fila" style="margin-top:12px"><button class="btn pri" onclick="limpaBordas()">Limpar bordas</button><span class="dica" style="margin:0">Corrige o que está marcado numa cópia, pra você comparar antes.</span></div>':'<div class="fila" style="margin-top:12px"><button class="btn" onclick="limpaBordas()">Procurar pontinhos soltos</button></div>'}</div>`}
let LIMPA=null;
async function limpaBordas(){const i=ISEL,box=$('#previaDS');
 box.innerHTML='<div class="placa pds"><p class="sub" style="margin:0">Procurando o que corrigir…</p></div>';
 const r=await api('/api/limpar',{tipo:i.tipo,rel:i.rel,png:i.mod,original:(ST.modo_ingles&&i.en)?i.en:i.jp});
 if(ISEL!==i)return;
 if(!r.ok){box.innerHTML=`<div class="placa pds"><p class="sub" style="margin:0">${esc(r.msg||'Não consegui limpar.')}</p></div>`;return}
 if(!r.corrigidos){box.innerHTML='<div class="placa pds"><h3>Limpar bordas</h3><p class="sub" style="margin:0 0 12px">Não achei nada pra corrigir. A imagem já está limpa.</p><button class="btn" onclick="previaDS()">Voltar pra prévia</button></div>';return}
 LIMPA=r.limpa;const ts=Date.now(),pl=(n,um,mais)=>n+' '+(n==1?um:mais),lin=[];
 if(r.buracos)lin.push(`<li>${pl(r.buracos,'buraco fechado','buracos fechados')} com a cor escura ao redor.</li>`);
 if(r.mudaram)lin.push(`<li>${pl(r.mudaram,'pixel','pixels')} de cor que o DS não tem ${r.mudaram==1?'virou':'viraram'} a cor vizinha.</li>`);
 if(r.soltos)lin.push(`<li>${pl(r.soltos,'pontinho solto removido','pontinhos soltos removidos')}.</li>`);
 if(r.restos)lin.push(`<li>${pl(r.restos,'resto da cor antiga coberto','restos da cor antiga cobertos')}.</li>`);
 if(r.sobrou)lin.push(`<li class="ruim">${pl(r.sobrou,'pixel ficou','pixels ficaram')} sem correção (sem cor boa por perto). Retoque à mão.</li>`);
 box.innerHTML=`<div class="placa pds"><h3>Limpar bordas: compare antes de aceitar</h3><ul class="resumoDS">${lin.join('')}</ul>
  <label class="chk" style="margin:4px 0 10px"><input type="checkbox" checked onchange="$('#limM').style.display=this.checked?'':'none'"> Piscar onde mudou</label>
  <div class="comp"><figure><figcaption>Como está</figcaption><img alt="" src="/f/${encodeURI(r.antes)}?${ts}"></figure>
   <figure><figcaption>Depois da limpeza</figcaption><div class="pdsImg"><img alt="" src="/f/${encodeURI(r.depois)}?${ts}"><img id="limM" alt="" src="/f/${encodeURI(r.marcas)}?${ts}"></div></figure></div>
  <div class="fila"><button class="btn pri" onclick="aplicaLimpeza()">Aplicar na imagem</button><button class="btn" onclick="previaDS()">Descartar</button></div>
  <p class="dica">Ao aplicar, a versão de agora fica guardada em backups\\limpeza. Feche a imagem no GIMP antes, ou reabra depois, pra não salvar a versão antiga por cima.</p></div>`}
async function aplicaLimpeza(){const i=ISEL;if(!LIMPA)return;
 const r=await api('/api/limpar',{aplicar:1,png:i.mod,limpa:LIMPA});LIMPA=null;
 if(!r.ok){avisa(r.msg||'Não consegui aplicar.',1);return}
 avisa('Limpeza aplicada. A versão anterior está em '+r.guardado.replace(/\//g,'\\'));
 await mostra(i.tipo+'/'+i.rel);previaDS()}
async function trazUma(orig){const i=ISEL;const subst=!!i.mod;if(subst&&!confirm('A skin '+ST.skin_atual+' já tem esta imagem. Substituir pela versão de '+orig+'?'))return;
 const r=await api('/api/skin',{acao:'copiar',nome:orig,novo:ST.skin_atual,itens:[i.tipo+'/'+i.rel],substituir:subst});avisa(r.msg,!r.ok);if(r.ok)mostra(i.tipo+'/'+i.rel)}
async function usaRef(){const r=await api('/api/referencia',{tipo:ISEL.tipo,rel:ISEL.rel});
 avisa(r.ok?'Cópia aberta. Ela fica em '+r.p.replace(/\//g,'\\')+' e nunca vai pro jogo.':'Não consegui abrir a cópia.',!r.ok)}
async function criaMod(original){if(original&&!confirm('Você já modificou esta imagem em outra skin. Começar mesmo da imagem original do jogo?'))return;
 const r=await api('/api/mod',{tipo:ISEL.tipo,rel:ISEL.rel,original:!!original});ISEL.mod=r.mod;
 avisa(r.de?'Versão modificada de '+r.de+' adicionada à skin '+ST.skin_atual+'.':'Imagem original adicionada à skin '+ST.skin_atual+'.');mostra(ISEL.tipo+'/'+ISEL.rel)}
async function editaMod(){const r=await api('/api/abrir',{p:ISEL.mod,gimp:true});if(!r.ok){ISEL.mod='';avisa('O arquivo do mod não existe mais. Clique em Criar mod pra puxar uma cópia nova.',true);mostra(ISEL.tipo+'/'+ISEL.rel)}}
document.addEventListener('keydown',e=>{if(e.target.matches('input,textarea'))return;if(e.key=='Escape')rodape.classList.remove('aberto')});
</script></body></html>"""


def main():
    porta = PORTA
    for tentativa in range(10):
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
            break
        except OSError:
            porta += 1
    else:
        print("Não consegui abrir o painel (portas ocupadas).")
        return
    url = "http://127.0.0.1:%d/" % porta
    sys.__stdout__.write("Painel Kira Game %s aberto em %s\nFeche esta janela pra encerrar.\n" % (VERSAO, url))
    sys.__stdout__.flush()
    log("Painel Kira Game %s pronto." % VERSAO)
    threading.Thread(target=backup_periodico, daemon=True).start()
    if "--sem-navegador" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

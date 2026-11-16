# -*- coding: utf-8 -*-
"""Cores novas nos mods e replicação de edições. Roda dentro do pipenv da ferramenta
(usa o hacktools e o game.py). Não precisa chamar direto:

  python auto.py repack            -> chama  "auto_cores.py paletas"  sozinho
  python auto.py replicar ORIGEM PADRAO

paletas   Para cada imagem em data/work_IMG, se você usou cores que não existem na
          paleta original, troca entradas da paleta que essa imagem não usa (ou que
          são repetidas) pelas cores novas. Grava o NCLR modificado em data/base_XAP
          (pra ferramenta converter certo) e em data/replace_XAP (pra ir pro jogo).
replicar  Aprende as trocas de cor que você fez numa imagem (ex.: blusa branca -> preta)
          e aplica nas outras imagens cujo caminho contém PADRAO, salvando em mods/.
"""
import json
import os
import shutil
import struct
import sys

import game
from hacktools import common, nitro
from PIL import Image

BASE_XAP = "data/base_XAP/"
EXTRACT_XAP = "data/extract_XAP/"
WORK = "data/work_IMG/"
REPLACE_XAP = "data/replace_XAP/"
REGISTRO = "data/auto_cores.json"
OUT_IMG = "data/out_IMG/"
MODS = "mods/IMG/"


# ------------------------------------------------------------------ NCLR bruto
def ler_nclr(caminho):
    """Lista de paletas na ordem do arquivo + onde cada cor fica no arquivo."""
    d = open(caminho, "rb").read()
    o = 0x10
    _, tam, bpp, _, _, _, desloc = struct.unpack_from("<4sIHHIII", d, o)
    cores = 256 if bpp == 4 else 16
    inicio = o + 8 + desloc
    num = (o + tam - inicio) // (cores * 2)
    pals = []
    for i in range(num):
        pals.append([struct.unpack_from("<H", d, inicio + (i * cores + j) * 2)[0] for j in range(cores)])
    return bytearray(d), pals, inicio, cores


def rgb(v):
    return ((v & 0x1f) << 3, ((v >> 5) & 0x1f) << 3, ((v >> 10) & 0x1f) << 3)


def p555(c):
    return (c[0] >> 3) | ((c[1] >> 3) << 5) | ((c[2] >> 3) << 10)


def snap(c):
    return (c[0] & 0xF8, c[1] & 0xF8, c[2] & 0xF8)


def dist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


# ------------------------------------------------------------------ áreas da imagem
def grupos(arq, palettes, ncgr, ncer):
    """Para cada célula: (chave_paleta, inicio_no_grupo, tamanho, retângulo no PNG)."""
    out = []
    altura = 0
    for bank in ncer.banks:
        if bank.width == 0 or bank.height == 0 or bank.duplicate:
            continue
        for cell in bank.cells:
            if cell.pal in palettes:
                chave, ini = cell.pal, 0
            else:
                chave, ini = 0, cell.pal * 16
            tam = 16 if ncgr.bpp == 4 else len(palettes[chave]) - ini
            out.append((chave, ini, tam, (cell.x, altura + cell.y, cell.width, cell.height)))
        altura += bank.height
    return out


def corte_mediano(cores, peso, k):
    """Junta muitas cores em k grupos rapidamente (degradês e pinturas suaves)."""
    caixas = [list(cores)]
    while len(caixas) < k:
        melhor = None
        for i, cx in enumerate(caixas):
            if len(cx) < 2:
                continue
            for canal in range(3):
                vals = [c[canal] for c in cx]
                faixa = (max(vals) - min(vals)) * sum(peso[c] for c in cx)
                if melhor is None or faixa > melhor[0]:
                    melhor = (faixa, i, canal)
        if melhor is None or melhor[0] == 0:
            break
        _, i, canal = melhor
        cx = sorted(caixas.pop(i), key=lambda c: c[canal])
        total = sum(peso[c] for c in cx)
        acc, corte = 0, 1
        for n, c in enumerate(cx):
            acc += peso[c]
            if acc >= total / 2:
                corte = max(1, min(len(cx) - 1, n + 1))
                break
        caixas += [cx[:corte], cx[corte:]]
    return caixas


def planejar(img, palettes, ncgr, ncer, brutas):
    """Decide quais cores novas entram em quais espaços da paleta.
    Devolve (trocas, log, por_grupo): trocas = [(idx_bruto, slot, (r, g, b))]."""
    px = img.load()
    por_grupo = {}
    for chave, ini, tam, (x0, y0, cw, ch) in grupos(None, palettes, ncgr, ncer):
        g = por_grupo.setdefault((chave, ini, tam), [])
        for y in range(max(0, y0), min(img.height, y0 + ch)):
            for x in range(max(0, x0), min(img.width, x0 + cw)):
                g.append((x, y))
    trocas = []
    log = []
    planejar.bruto_do_grupo = {}
    for (chave, ini, tam), pontos in por_grupo.items():
        pal = palettes[chave]
        # qual paleta do arquivo é essa?
        idx_bruto = next((i for i, b in enumerate(brutas) if [rgb(v) for v in b] == [c[:3] for c in pal]), None)
        planejar.bruto_do_grupo[(chave, ini, tam)] = idx_bruto
        if idx_bruto is None:
            continue
        slots = list(range(ini, ini + tam))
        atuais = {s: rgb(brutas[idx_bruto][s]) for s in slots}
        usadas = {}
        for (x, y) in pontos:
            c = px[x, y]
            if c[3] == 0:
                continue
            usadas[snap(c[:3])] = usadas.get(snap(c[:3]), 0) + 1
        primeiro = {}
        for s in slots[1:]:  # o slot 0 do grupo é a transparência
            primeiro.setdefault(atuais[s], s)
        novas = [c for c in usadas if c not in primeiro]
        if not novas:
            continue
        # cores muito parecidas com uma existente não gastam espaço
        existentes = list(primeiro)
        restantes = []
        for c in novas:
            perto = min(existentes, key=lambda e: dist(e, c)) if existentes else None
            if perto is not None and dist(perto, c) <= 3 * 8 * 8:
                continue
            restantes.append(c)
        reservados = {primeiro[c] for c in usadas if c in primeiro}
        livres = [s for s in slots[1:] if s not in reservados]
        if not restantes or not livres:
            if restantes:
                log.append("sem espaço na paleta: %d cores novas viram a mais próxima" % len(restantes))
            continue
        # se faltar espaço, junta as cores novas mais parecidas
        if len(restantes) > 200:
            grupos_c = corte_mediano(restantes, usadas, len(livres))
        else:
            grupos_c = [[c] for c in restantes]
        while len(grupos_c) > len(livres):
            melhor = None
            for i in range(len(grupos_c)):
                for j in range(i + 1, len(grupos_c)):
                    dd = dist(grupos_c[i][0], grupos_c[j][0])
                    if melhor is None or dd < melhor[0]:
                        melhor = (dd, i, j)
            _, i, j = melhor
            grupos_c[i] += grupos_c.pop(j)
        for slot, gc in zip(livres, grupos_c):
            peso = sum(usadas[c] for c in gc)
            media = tuple(sum(c[k] * usadas[c] for c in gc) // peso for k in range(3))
            trocas.append((idx_bruto, slot, media))
        log.append("%d cores novas na paleta (%d espaços livres)" % (len(restantes), len(livres)))
    return trocas, log, por_grupo


def processar(rel, registro):
    """Retorna texto de log, ou None se não precisou mexer."""
    tronco = rel[:-4]
    nclr = BASE_XAP + tronco + ".NCLR"
    if not os.path.isfile(nclr) or not os.path.isfile(BASE_XAP + tronco + ".NCGR"):
        return None
    orig = nclr + ".orig"
    if not os.path.isfile(orig):
        shutil.copy2(nclr, orig)
    shutil.copy2(orig, nclr)  # sempre parte da paleta original
    palettes, ncgr, nscr, ncer, w, h, _, _ = game.readImage(BASE_XAP, tronco + ".NCGR", ".NCGR")
    if ncer is None or ncgr is None:
        return None  # só imagens com células (retratos, menus); mapas ficam como estão
    dados, brutas, inicio, ncores = ler_nclr(nclr)
    img = Image.open(WORK + rel).convert("RGBA")
    trocas, log, _ = planejar(img, palettes, ncgr, ncer, brutas)
    if not trocas:
        shutil.copy2(orig, nclr)
        return "; ".join(log) if log else None
    for idx_bruto, slot, cor in trocas:
        struct.pack_into("<H", dados, inicio + (idx_bruto * ncores + slot) * 2, p555(cor))
    open(nclr, "wb").write(dados)
    common.makeFolders(os.path.dirname(REPLACE_XAP + tronco))
    shutil.copy2(nclr, REPLACE_XAP + tronco + ".NCLR")
    registro[tronco] = True
    return "; ".join(log)


# ------------------------------------------------------------------ prévia no DS
LIMITE = 40 * 40  # diferença de cor a partir da qual um pixel "muda bastante"


def simular(rel, png, original=None):
    """Calcula como cada pixel da imagem vai ficar no jogo. Devolve um dicionário com
    "ok" e, se deu certo: img, larg, ds (imagem simulada), transp, visivel, buracos,
    ruins (pixels que mudam bastante), avisos, cores_novas."""
    import tempfile
    tronco = rel[:-4]
    r = {"ok": False}
    pasta_base = BASE_XAP
    if not os.path.isfile(BASE_XAP + tronco + ".NCLR") or not os.path.isfile(BASE_XAP + tronco + ".NCGR"):
        pasta_base = EXTRACT_XAP
    if not os.path.isfile(pasta_base + tronco + ".NCGR"):
        r["msg"] = "Não achei os arquivos do jogo desta imagem. Rode a extração primeiro."
        return r
    # lê a imagem a partir da paleta original, sem mexer nos arquivos do projeto
    tmp = tempfile.mkdtemp(prefix="kira_previa_")
    try:
        for ext in (".NCGR", ".NCLR", ".NCER", ".NSCR"):
            src = pasta_base + tronco + ext
            if ext == ".NCLR" and os.path.isfile(src + ".orig"):
                src = src + ".orig"
            if os.path.isfile(src):
                dst = os.path.join(tmp, tronco + ext)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        palettes, ncgr, nscr, ncer, w, h, _, _ = game.readImage(tmp + "/", tronco + ".NCGR", ".NCGR")
        if ncer is None or ncgr is None:
            r["msg"] = "Isso funciona com retratos e menus. Esta imagem é de outro tipo; teste no jogo."
            return r
        dados, brutas, inicio, ncores = ler_nclr(os.path.join(tmp, tronco + ".NCLR"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    img = Image.open(png).convert("RGBA")
    trocas, log, por_grupo = planejar(img, palettes, ncgr, ncer, brutas)
    bruto_do_grupo = dict(planejar.bruto_do_grupo)
    px = img.load()
    larg = img.width - 40 if img.width > 40 else img.width
    ds = Image.new("RGBA", (larg, img.height), (0, 0, 0, 0))
    pd = ds.load()
    transp, visivel, ruins = set(), set(), set()
    n = 16 if ncgr.bpp == 4 else -1
    finais = {}
    for grupo, pontos in por_grupo.items():
        chave, ini, tam = grupo
        # paleta final: a do jogo com as cores novas que o repack vai colocar
        final = [tuple(c[:3]) + (255,) for c in palettes[chave]]
        ib = bruto_do_grupo.get(grupo)
        for t_ib, slot, cor in trocas:
            if t_ib == ib and ib is not None and slot < len(final):
                final[slot] = tuple(cor) + (255,)
        finais[grupo] = final
        for (x, y) in pontos:
            if x >= larg:
                continue
            c = px[x, y]
            i = common.getPaletteIndex(final, c, False, ini, n, False, True)
            if i == 0:
                pd[x, y] = (0, 0, 0, 0)
                transp.add((x, y))
                continue
            nc = final[ini + i]
            pd[x, y] = nc
            visivel.add((x, y))
            if c[3] and dist(nc, c) > LIMITE:
                ruins.add((x, y))
    # buracos: pixels que eram desenho na imagem original e agora viram transparentes
    # (no jogo, o cenário apareceria através deles)
    buracos = set()
    po = None
    if original and os.path.isfile(original):
        io = Image.open(original).convert("RGBA")
        if io.size == img.size:
            po = io.load()
            for grupo, pontos in por_grupo.items():
                chave, ini, tam = grupo
                pal = [tuple(c[:3]) + (255,) for c in palettes[chave]]
                for (x, y) in pontos:
                    if (x, y) in transp and po[x, y][3] and \
                       common.getPaletteIndex(pal, po[x, y], False, ini, n, False, True) != 0:
                        buracos.add((x, y))
    r.update(ok=True, img=img, larg=larg, ds=ds, transp=transp, visivel=visivel, buracos=buracos,
             ruins=ruins, avisos=log, cores_novas=len(trocas), por_grupo=por_grupo, finais=finais,
             original=po)
    return r


def previa(rel, png, saida, original=None):
    """Simula como a imagem vai ficar no jogo, sem gerar a ROM.
    Grava saida + "_ds.png" (como o jogo mostra), saida + "_marcas.png" (problemas)
    e devolve o resumo."""
    s = simular(rel, png, original)
    if not s["ok"]:
        return s
    marcas = Image.new("RGBA", (s["larg"], s["img"].height), (0, 0, 0, 0))
    pm = marcas.load()
    for (x, y) in s["ruins"]:
        pm[x, y] = (255, 40, 60, 255)
    for (x, y) in s["buracos"]:
        pm[x, y] = (40, 230, 255, 255)
    s["ds"].save(saida + "_ds.png")
    marcas.save(saida + "_marcas.png")
    return {"ok": True, "mudaram": len(s["ruins"]), "buracos": len(s["buracos"]),
            "avisos": s["avisos"], "cores_novas": s["cores_novas"]}


# ------------------------------------------------------------------ limpar bordas
VIZ = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def lum(c):
    return (c[0] * 299 + c[1] * 587 + c[2] * 114) // 1000


def limpar(rel, png, saida, original=None):
    """Corrige sozinho os problemas que a prévia mostra e grava uma CÓPIA em
    saida + "_limpa.png" (a imagem do mod não é alterada). Também grava a prévia da cópia
    em saida + "_limpa_ds.png" e as marcas do que mudou em saida + "_limpa_marcas.png"."""
    s = simular(rel, png, original)
    if not s["ok"]:
        return s
    img, larg, ds = s["img"], s["larg"], s["ds"]
    px, pd, po = img.load(), ds.load(), s["original"]
    alt = img.height
    visivel, buracos, ruins = s["visivel"], s["buracos"], s["ruins"]
    grupo_de = {}
    for grupo, pontos in s["por_grupo"].items():
        for p in pontos:
            grupo_de[p] = grupo
    mudados = {}

    def vizinhos_bons(x, y, ruim):
        cont = {}
        for dx, dy in VIZ:
            v = (x + dx, y + dy)
            if v in visivel and v not in ruim and v not in buracos:
                c = mudados.get(v) or pd[v[0], v[1]]
                cont[c] = cont.get(c, 0) + 1
        return cont

    # 1) buracos: viram contorno (a cor mais escura da paleta) ou a cor vizinha
    for (x, y) in buracos:
        final = s["finais"][grupo_de[(x, y)]]
        ini, tam = grupo_de[(x, y)][1], grupo_de[(x, y)][2]
        cont = vizinhos_bons(x, y, ruins)
        escuras = sorted((c for c in cont if lum(c) < 90), key=lum)
        if escuras:
            mudados[(x, y)] = escuras[0]
        elif cont:
            mudados[(x, y)] = max(cont, key=cont.get)
        else:
            mudados[(x, y)] = min(final[ini + 1:ini + tam], key=lum)
    n_buracos = len(buracos)
    # 2) pixels que mudam bastante de cor: assumem a cor boa mais comum ao redor
    pendentes = set(ruins)
    n_ruins = 0
    for _ in range(6):
        feitos = {}
        for (x, y) in pendentes:
            cont = vizinhos_bons(x, y, pendentes)
            if cont:
                feitos[(x, y)] = max(cont, key=cont.get)
        if not feitos:
            break
        mudados.update(feitos)
        pendentes -= set(feitos)
        n_ruins += len(feitos)
    # 3) pontinhos soltos em área editada: pixel sem nenhum vizinho de cor parecida
    n_soltos = 0
    PARECIDA = 3 * 28 * 28
    for (x, y) in visivel:
        if (x, y) in mudados or (x, y) in ruins:
            continue
        if po is not None and po[x, y] == px[x, y]:
            continue  # igual ao original: faz parte do desenho do jogo
        c = pd[x, y]
        cont = vizinhos_bons(x, y, ruins)
        if sum(cont.values()) < 5:
            continue  # na beirada do desenho: não dá pra julgar
        if not any(dist(c, v) <= PARECIDA for v in cont):
            mudados[(x, y)] = max(cont, key=cont.get)
            n_soltos += 1
    # 4) restos da cor antiga: pixel claro que ficou igual ao original, cercado de pixels editados
    n_restos = 0
    if po is not None:
        for (x, y) in visivel:
            if (x, y) in mudados or po[x, y] != px[x, y] or lum(pd[x, y]) < 70:
                continue
            editados = {}
            for dx, dy in VIZ:
                v = (x + dx, y + dy)
                if v in visivel and v not in buracos and 0 <= v[0] < larg and 0 <= v[1] < alt \
                   and po[v[0], v[1]] != px[v[0], v[1]]:
                    c = mudados.get(v) or pd[v[0], v[1]]
                    editados[c] = editados.get(c, 0) + 1
            if sum(editados.values()) >= 6 and not any(dist(pd[x, y], c) <= PARECIDA for c in editados):
                mudados[(x, y)] = max(editados, key=editados.get)
                n_restos += 1
    limpa = img.copy()
    pl = limpa.load()
    marcas = Image.new("RGBA", (larg, alt), (0, 0, 0, 0))
    pm = marcas.load()
    for (x, y), c in mudados.items():
        pl[x, y] = tuple(c[:3]) + (255,)
        pm[x, y] = (255, 220, 40, 255)
    limpa.save(saida + "_limpa.png")
    marcas.save(saida + "_limpa_marcas.png")
    depois = simular(rel, saida + "_limpa.png", original)
    if depois["ok"]:
        depois["ds"].save(saida + "_limpa_ds.png")
    ds.save(saida + "_ds.png")
    return {"ok": True, "corrigidos": len(mudados), "buracos": n_buracos, "mudaram": n_ruins,
            "soltos": n_soltos, "restos": n_restos, "sobrou": len(pendentes),
            "depois_mudaram": len(depois["ruins"]) if depois["ok"] else 0,
            "depois_buracos": len(depois["buracos"]) if depois["ok"] else 0}


def paletas():
    registro = {}
    if os.path.isfile(REGISTRO):
        with open(REGISTRO, encoding="utf-8") as f:
            registro = json.load(f)
    novo = {}
    if os.path.isdir(WORK):
        for rel in common.getFiles(WORK, ".png"):
            try:
                r = processar(rel, novo)
            except Exception as e:
                r = "não consegui analisar (%s)" % e
            if r:
                print("  [cores] %s: %s" % (rel, r))
    # desfaz o que não precisa mais
    for tronco in registro:
        if tronco not in novo:
            nclr = BASE_XAP + tronco + ".NCLR"
            if os.path.isfile(nclr + ".orig"):
                shutil.copy2(nclr + ".orig", nclr)
            if os.path.isfile(REPLACE_XAP + tronco + ".NCLR"):
                os.remove(REPLACE_XAP + tronco + ".NCLR")
    with open(REGISTRO, "w", encoding="utf-8") as f:
        json.dump(novo, f, indent=1)


# ------------------------------------------------------------------ replicar
def replicar(origem, padrao, forcar=False, pasta=None):
    global MODS
    if pasta:
        MODS = pasta
    rel = next((r for r in common.getFiles(MODS, ".png") if origem.lower() in r.lower()), None)
    if rel is None:
        print("  Não achei '%s' na pasta mods. Edite a imagem lá primeiro." % origem)
        return
    a = Image.open(OUT_IMG + rel).convert("RGBA")
    b = Image.open(MODS + rel).convert("RGBA")
    if a.size != b.size:
        print("  A imagem editada mudou de tamanho; não dá pra comparar.")
        return
    pa, pb = a.load(), b.load()
    total, trocas = {}, {}
    for y in range(a.height):
        for x in range(a.width - 40):  # ignora a faixa de paleta
            c = pa[x, y]
            if c[3] == 0:
                continue
            total[c[:3]] = total.get(c[:3], 0) + 1
            n = pb[x, y]
            if n != c:
                t = trocas.setdefault(c[:3], {})
                t[n] = t.get(n, 0) + 1
    mapa = {}
    for c, t in trocas.items():
        n, qtd = max(t.items(), key=lambda kv: kv[1])
        if sum(t.values()) >= 0.6 * total[c] and qtd >= 0.6 * sum(t.values()):
            mapa[c] = n
    if not mapa:
        print("  Não encontrei trocas de cor claras nessa imagem.")
        return
    print("  Aprendi %d trocas de cor em %s" % (len(mapa), rel))
    chaves = list(mapa)
    feitos = []
    for alvo in common.getFiles(OUT_IMG, ".png"):
        if alvo == rel or padrao.lower() not in alvo.lower():
            continue
        destino = MODS + alvo
        if os.path.isfile(destino) and not forcar:
            print("  [pulei] %s já existe em mods (use --forcar pra sobrescrever)" % alvo)
            continue
        im = Image.open(OUT_IMG + alvo).convert("RGBA")
        p = im.load()
        larg = im.width - 40
        n = 0
        for y in range(im.height):
            for x in range(larg):
                c = p[x, y]
                if c[3] == 0:
                    continue
                k = c[:3]
                if k not in mapa:  # tolerância: cores quase iguais de outra paleta
                    perto = min(chaves, key=lambda e: dist(e, k))
                    if dist(perto, k) > 3 * 6 * 6:
                        continue
                    k = perto
                p[x, y] = mapa[k]
                n += 1
        if n:
            common.makeFolders(os.path.dirname(destino))
            im.save(destino)
            feitos.append((alvo, n))
    with open("data/replicar.json", "w", encoding="utf-8") as f:
        json.dump({"origem": rel, "feitos": feitos}, f, ensure_ascii=False)
    print("  %d imagens criadas em mods" % len(feitos))


def variar(origem, destino, graus):
    """Cria uma skin nova girando o tom (matiz) de todas as imagens de outra skin."""
    import colorsys
    graus = float(graus) / 360.0
    n = 0
    for raiz, _, arqs in os.walk(origem):
        for a in arqs:
            src = os.path.join(raiz, a)
            alvo = os.path.join(destino, os.path.relpath(src, origem))
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            if not a.lower().endswith(".png"):
                continue
            im = Image.open(src).convert("RGBA")
            px = im.load()
            cache = {}
            for y in range(im.height):
                for x in range(im.width):
                    c = px[x, y]
                    if c[3] == 0:
                        continue
                    if c not in cache:
                        h, s_, v = colorsys.rgb_to_hsv(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
                        r, g, b = colorsys.hsv_to_rgb((h + graus) % 1.0, s_, v)
                        cache[c] = (int(r * 255), int(g * 255), int(b * 255), c[3])
                    px[x, y] = cache[c]
            im.save(alvo)
            n += 1
    print("  %d imagens com o tom girado" % n)


if __name__ == "__main__":
    pasta = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--pasta=")), None)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args[:1] == ["paletas"]:
        paletas()
    elif args[:1] == ["replicar"] and len(args) >= 3:
        replicar(args[1], args[2], "--forcar" in sys.argv, pasta)
    elif args[:1] == ["previa"] and len(args) >= 4:
        r = previa(args[1], args[2], args[3], args[4] if len(args) > 4 else None)
        with open(args[3] + ".json", "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
    elif args[:1] == ["limpar"] and len(args) >= 4:
        r = limpar(args[1], args[2], args[3], args[4] if len(args) > 4 else None)
        with open(args[3] + ".json", "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
    elif args[:1] == ["variar"] and len(args) >= 4:
        variar(args[1], args[2], args[3])
    else:
        print(__doc__)

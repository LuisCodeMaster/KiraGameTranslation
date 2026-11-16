# -*- coding: utf-8 -*-
"""Procura imagens extraídas com a paleta errada e testa a correção que o AGT Team
usa (ignorar a tabela de índices da paleta). Rode com:  python auto.py paletas
(roda dentro do pipenv da ferramenta, porque usa o hacktools e o game.py)."""
import json
import os
import shutil

import game
from hacktools import common, nitro
from PIL import Image

ENTRADA = "data/extract_XAP/"
TESTE = "data/paleta_teste/"
LISTA = "paletas_corrigidas.txt"


def desenhar(arquivo, ignorar, saida):
    ext = os.path.splitext(arquivo)[1]
    pal, img, mapa, cel, w, h = nitro.readNitroGraphic(
        ENTRADA + arquivo.replace(ext, ".NCLR"), ENTRADA + arquivo,
        ENTRADA + arquivo.replace(ext, ".NSCR"), ENTRADA + arquivo.replace(ext, ".NCER"), ignorar)
    if img is None:
        return None
    common.makeFolders(os.path.dirname(saida))
    if cel is not None:
        nitro.drawNCER(saida, cel, img, pal, True, False)
    else:
        nitro.drawNCGR(saida, mapa, img, pal, w, h)
    return pal


def aspereza(caminho):
    """Média da diferença de cor entre pixels vizinhos (cores certas = imagem mais 'lisa')."""
    im = Image.open(caminho).convert("RGBA")
    w, h = im.size
    w = max(1, w - 40)  # ignora a faixa de paleta desenhada à direita
    px = im.load()
    soma = n = 0
    for y in range(0, h, 2):
        for x in range(0, w - 1):
            a, b = px[x, y], px[x + 1, y]
            if a[3] and b[3]:
                soma += abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
                n += 1
    return soma / n if n > 200 else None


def main():
    if not os.path.isdir(ENTRADA):
        print("Falta data/extract_XAP (rode o extract com 'rom' e 'img' marcados).")
        return
    shutil.rmtree(TESTE, ignore_errors=True)
    ja = set(game.ignorepalindex)
    resultados = []
    arquivos = [a for a in common.getFiles(ENTRADA, ".NCGR") if a not in ja]
    for arq in common.showProgress(arquivos):
        ext = os.path.splitext(arq)[1]
        nclr = ENTRADA + arq.replace(ext, ".NCLR")
        if not os.path.isfile(nclr):
            continue
        try:
            if nitro.readNCLR(nclr, False) == nitro.readNCLR(nclr, True):
                continue  # a correção não muda nada nessa imagem
            png = arq.replace(ext, ".png")
            if desenhar(arq, False, TESTE + "normal/" + png) is None:
                continue
            desenhar(arq, True, TESTE + "corrigida/" + png)
            a1 = aspereza(TESTE + "normal/" + png)
            a2 = aspereza(TESTE + "corrigida/" + png)
        except Exception as e:
            print("  [pulei]", arq, "-", e)
            continue
        if a1 is None or a2 is None:
            continue
        resultados.append({"arq": arq, "png": png, "normal": round(a1, 1), "corrigida": round(a2, 1),
                           "sugere": a2 < a1 * 0.85})
    with open(TESTE + "resultado.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=1)
    print("  %d imagens afetadas pela tabela de paletas, %d parecem estar com a paleta errada"
          % (len(resultados), sum(r["sugere"] for r in resultados)))


if __name__ == "__main__":
    main()

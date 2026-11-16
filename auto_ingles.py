# -*- coding: utf-8 -*-
"""Lê a ROM com o patch em inglês e gera data/en_ref.json com o texto em inglês
de cada frase japonesa. Não precisa rodar direto: use  python auto.py ingles
(roda dentro do pipenv da ferramenta, porque usa o hacktools e o game.py)."""
import json
import os
import struct
import sys

import game
from hacktools import common, nds, nitro

ORIG = "data/extract/"
EN = "data/en_extract/"
SAIDA = "data/en_ref.json"
PTR = 0x02000000


def ler_en(dados, pos):
    """Lê uma frase do arm9 em inglês (ASCII + Shift-JIS + códigos de cor)."""
    out, i = "", pos
    while 0 <= i < len(dados) and len(out) < 600:
        b = dados[i]
        if b == 0:
            break
        if b == 0x03 and i + 1 < len(dados):
            out += "<col%02x>" % dados[i + 1]
            i += 2
            continue
        if b == 0x0A:
            out += "|"
        elif 0x20 <= b < 0x7F:
            out += chr(b)
        elif (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC) and i + 1 < len(dados):
            try:
                out += dados[i:i + 2].decode("shift_jis")
            except UnicodeDecodeError:
                out += "<%02x><%02x>" % (b, dados[i + 1])
            i += 2
            continue
        else:
            out += "<%02x>" % b
        i += 1
    return out


def bin_ingles():
    orig = open(ORIG + "arm9.bin", "rb").read()
    en = open(EN + "arm9.bin", "rb").read()
    strings, positions = common.extractBinaryStrings(ORIG + "arm9.bin", game.binranges, game.readBINString, "shift_jis")
    ref = {}
    for s, poss in zip(strings, positions):
        alvo = None
        for pos in poss:
            idx = orig.find(struct.pack("<I", PTR + pos))
            if idx >= 0 and idx + 4 <= len(en):
                novo = struct.unpack_from("<I", en, idx)[0] - PTR
                if 0 <= novo < len(en):
                    alvo = novo
                    break
        if alvo is None:
            alvo = poss[0]
        txt = ler_en(en, alvo).strip()
        if txt and txt != s:
            ref[s] = txt
    return ref


def scenario_ingles():
    pasta_o = ORIG + "data/data/scenario/"
    pasta_e = EN + "data/data/scenario/"
    ref = {}
    for arq in common.getFiles(pasta_o, ".bin"):
        if not os.path.isfile(pasta_e + arq):
            continue
        po = game.readScenario(pasta_o + arq)
        pe = game.readScenario(pasta_e + arq)
        lista = []
        for a, b in zip(po, pe):
            for so, se in zip(a.strings, b.strings):
                jp = so.sjis[:-4] if so.sjis.endswith("<1f>") else so.sjis
                tx = se.sjis[:-4] if se.sjis.endswith("<1f>") else se.sjis
                lista.append([jp, tx if tx != jp else ""])
        ref[arq] = lista
    return ref


def extrair_xap_en():
    entrada = EN + "data/data/window/"
    saida = "data/en_extract_XAP/"
    common.makeFolder(saida)
    for arq in common.getFiles(entrada, ".xap"):
        with common.Stream(entrada + arq, "rb") as f:
            f.seek(4)
            num = f.readUShort()
            for i in range(num):
                f.seek(16 + 16 * i)
                tipo = f.readString(4)
                t1 = f.readUInt()
                t2 = f.readUInt()
                if t1 != t2:
                    continue
                off = f.readUInt()
                nome = game.convertXAPName(arq, tipo)
                common.makeFolders(saida + os.path.dirname(arq))
                f.seek(off)
                with common.Stream(saida + nome, "wb") as fout:
                    fout.write(f.read(t1))


def diferentes(pasta_jp, pasta_en):
    from PIL import Image
    lista = []
    for arq in common.getFiles(pasta_jp, ".png"):
        if not os.path.isfile(pasta_en + arq):
            continue
        try:
            a = Image.open(pasta_jp + arq).convert("RGBA")
            b = Image.open(pasta_en + arq).convert("RGBA")
            if a.size != b.size or a.tobytes() != b.tobytes():
                lista.append(arq)
        except Exception:
            continue
    return lista


def imagens_ingles():
    print("Extraindo imagens em inglês...")
    extrair_xap_en()
    nitro.extractIMG("data/en_extract_XAP/", "data/en_IMG/", readfunc=game.readImage)
    if os.path.isdir(EN + "data/data/model/"):
        nitro.extractNSBMD(EN + "data/data/model/", "data/en_NSBMD/", readfunc=game.readNSBMD)
    res = {"IMG": diferentes("data/out_IMG/", "data/en_IMG/"),
           "NSBMD": diferentes("data/out_NSBMD/", "data/en_NSBMD/") if os.path.isdir("data/en_NSBMD/") else []}
    with open("data/en_imagens.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("  ", len(res["IMG"]), "imagens e", len(res["NSBMD"]), "texturas 3D com texto (editadas no patch em inglês)")


def main():
    if len(sys.argv) > 2 and sys.argv[2] == "--so-imagens":
        imagens_ingles()
        return
    rom = sys.argv[1]
    if not os.path.isfile(rom):
        print("ROM em inglês não encontrada:", rom)
        sys.exit(1)
    if not os.path.isfile(ORIG + "arm9.bin"):
        print("Rode o extract da ferramenta antes (falta data/extract).")
        sys.exit(1)
    print("Extraindo a ROM em inglês para", EN, "...")
    nds.extractRom(rom, EN)
    print("Lendo menus (bin)...")
    b = bin_ingles()
    print("  ", len(b), "frases em inglês encontradas")
    print("Lendo história (scenario)...")
    s = scenario_ingles()
    print("  ", sum(len(v) for v in s.values()), "frases lidas em", len(s), "arquivos")
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump({"bin": b, "scenario": s}, f, ensure_ascii=False)
    print("Referência salva em", SAIDA)
    if os.path.isdir("data/out_IMG/"):
        try:
            imagens_ingles()
        except Exception as e:
            print("  [aviso] não consegui comparar as imagens:", e)
    else:
        print("  [aviso] data/out_IMG não existe: rode o extract com 'img' marcado pra comparar as imagens")


if __name__ == "__main__":
    main()

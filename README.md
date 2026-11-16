# Painel Kira Game

Painel para traduzir e criar mods visuais de **Death Note: Kira Game** (Nintendo DS), em português.

Este ramo (`painel`) é um fork do [KiraGameTranslation](https://github.com/AGTTeam/KiraGameTranslation), a ferramenta da AGT Team que extrai e remonta o jogo. O painel roda em cima dela e acrescenta uma interface no navegador, um sistema de skins e várias automações.

> Este repositório tem **só código**. Ele não inclui a ROM, imagens, textos nem fontes do jogo. Você precisa da sua própria cópia do jogo, extraída do seu cartucho.

## O que o painel faz

**Tradução**
- Planilhas de tradução com o texto original e o inglês lado a lado, como referência.
- Prévia de cada frase numa moldura do DS, com aviso quando o texto não cabe.
- Suporte a acentos do português (á, ã, ç, é, õ...).
- Modo "base em inglês": o que você ainda não traduziu aparece em inglês no jogo, em vez de japonês.
- Glossário de termos.

**Imagens e skins**
- Galeria de todas as imagens do jogo, com busca e filtro por pasta.
- Skins: conjuntos de imagens editadas que você liga, desliga, ordena por prioridade, duplica, exporta e importa em `.zip`.
- Variações de cor de uma skin inteira.
- Replicar: aprende as trocas de cor de um retrato e aplica nos parecidos.
- Cores novas na paleta: o painel aproveita os espaços livres da paleta de cada retrato.
- Prévia no DS: mostra como a imagem editada vai ficar no jogo, sem gerar a ROM, e marca buracos e cores que o DS não consegue mostrar.
- Limpar bordas: corrige pontinhos e restos de cor numa cópia, pra você comparar antes de aceitar.

**Projeto**
- Gerar a ROM e abrir no emulador com um clique.
- Gerar o patch de lançamento (`.xdelta`).
- Backup automático diário das traduções e skins.
- Programa em `.exe`, pra abrir sem linha de comando.

## O que você precisa

- Windows
- [Python 3](https://www.python.org/downloads/)
- [ImageMagick](https://imagemagick.org/script/download.php) (na instalação, marque "Add application directory to your system path")
- A ROM japonesa original do jogo, extraída do seu cartucho
- Um emulador de DS para testar, como o [melonDS](https://melonds.kuribo64.net/)
- [GIMP](https://www.gimp.org/), ou outro editor, para editar as imagens

## Como instalar

1. Baixe este ramo (botão **Code → Download ZIP**) e extraia.
2. Copie a sua ROM para a pasta e renomeie para `dn1.nds`.
3. Rode `run_windows.bat` uma vez. Ele instala as dependências e abre a ferramenta original; use **extract** para extrair o jogo e feche.
4. Rode `painel.bat`. O painel abre no navegador, em `127.0.0.1:8765`.
5. Em **Configurações**, indique onde está o melonDS.

A tela **Início** mostra o que falta preparar e o próximo passo.

### Programa em .exe (opcional)

Rode `build_exe.bat`. Ele cria a pasta `PainelKiraGame` com o `PainelKiraGame.exe`. Depois disso, é só abrir o programa; para atualizar, basta trocar os arquivos `.py` da pasta.

## Como funciona por dentro

| Arquivo | Função |
| --- | --- |
| `painel.py` | Servidor local e interface do painel |
| `auto.py` | Automação: planilhas, skins, backups, geração da ROM |
| `auto_cores.py` | Paletas, replicar, variações, prévia no DS e limpar bordas |
| `auto_ingles.py` | Lê a versão em inglês para usar como referência |
| `auto_paletas.py` | Encontra retratos com cores erradas |
| `tool.py`, `game.py`, `repack_sce.py` | Ferramenta original da AGT Team, com pequenos ajustes |

As pastas de trabalho (`data`, `traducao`, `skins`, `backups`...) são criadas no seu PC e ficam fora do repositório.

## Avisos

- Não peça nem compartilhe ROMs aqui. Traduções e skins são distribuídas como patch, para aplicar na sua própria cópia do jogo.
- A fonte com acentos não está no repositório, por ser derivada da fonte do jogo.
- Projeto de fã, sem fins lucrativos e sem ligação com os donos de Death Note ou com a Konami.

## Créditos

- [AGT Team](https://agtteam.net/kiragame): ferramenta KiraGameTranslation e a tradução em inglês do jogo.
- [Illidanz](https://github.com/Illidanz/hacktools): biblioteca hacktools.
- Painel e automações: [LuisCodeMaster](https://github.com/LuisCodeMaster).

O código original é distribuído sob a licença MIT (veja `LICENSE`); as adições deste ramo seguem a mesma licença.

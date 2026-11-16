@echo off
chcp 65001 >NUL
cd /d "%~dp0"
:menu
echo.
echo ============ Traducao / Mod Kira Game ============
echo  1 - Criar planilhas de traducao
echo  2 - Puxar referencia em ingles (ROM com patch EN)
echo  3 - Aplicar + gerar ROM + jogar
echo  4 - Aplicar + gerar ROM + jogar (apagando o save)
echo  5 - Modo automatico (salvou, atualiza o jogo)
echo  6 - Previa dos textos com a fonte do jogo
echo  7 - Preparar imagens com texto + galeria
echo  8 - Abrir galeria de imagens com texto
echo  9 - Catalogo de TODAS as imagens (roupas, retratos...)
echo 10 - Copiar imagem pra pasta mods (pede o nome)
echo 11 - Modo INGLES (o que falta traduzir fica em ingles)
echo 12 - Modo PORTUGUES (so o que voce traduziu)
echo 13 - Ver progresso
echo 14 - Gerar patch para lancamento
echo 15 - Achar imagens com cores erradas
echo 16 - Reextrair imagens com as cores corrigidas
echo 17 - Replicar edicao de cores em outras imagens
echo  0 - Sair
echo.
set "op="
set /p op=Escolha: 
if "%op%"=="1" python auto.py planilha
if "%op%"=="2" python auto.py ingles
if "%op%"=="3" python auto.py tudo
if "%op%"=="4" python auto.py tudo --limpo
if "%op%"=="5" python auto.py vigiar
if "%op%"=="6" python auto.py previa
if "%op%"=="7" python auto.py imagens
if "%op%"=="8" python auto.py galeria
if "%op%"=="9" python auto.py catalogo
if "%op%"=="10" (
  set "nm="
  set /p nm=Parte do nome da imagem: 
  call python auto.py mod %%nm%%
)
if "%op%"=="11" python auto.py modo ingles
if "%op%"=="12" python auto.py modo portugues
if "%op%"=="13" python auto.py status
if "%op%"=="14" python auto.py lancar
if "%op%"=="15" python auto.py paletas
if "%op%"=="16" python auto.py reextrair
if "%op%"=="17" (
  set "orig="
  set "pad="
  set /p orig=Imagem editada (ex.: char003u): 
  set /p pad=Aplicar nas imagens com este nome (ex.: char003): 
  call python auto.py replicar %%orig%% %%pad%%
)
if "%op%"=="0" exit /b
goto menu

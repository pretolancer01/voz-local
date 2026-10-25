# Voz Local

Aplicativo de desktop para gerar narrações com a própria voz, usando modelos gratuitos executados no computador. Roteiros longos são divididos em trechos, salvos progressivamente e podem ser retomados. O áudio de referência e os resultados permanecem em arquivos locais.

## Recursos

- Chatterbox Multilingual V3 e ajuste para português brasileiro.
- Entrada de referência M4A, MP3 ou WAV; roteiro digitado ou importado de TXT.
- Verificação local da fala com Whisper, repetição de trechos com falha e exportação em WAV.
- Sem conta, chave de API ou cota de palavras do aplicativo. O roteiro é dividido em trechos de até 200 caracteres; o tempo e o espaço em disco crescem com seu tamanho.

## Instalação no Windows

Requer Python 3.12, FFmpeg fornecido pelo pacote `imageio-ffmpeg` e PyTorch compatível com a GPU. Para Radeon RX 9070 XT no Windows, siga a [documentação da AMD para PyTorch com ROCm](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html) antes de instalar os pacotes abaixo. Para outras GPUs, use a instalação recomendada pelo [PyTorch](https://pytorch.org/get-started/locally/).

```powershell
py -3.12 -m venv .venv
# Instale torch e torchaudio compatíveis com sua GPU no ambiente .venv.
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps "git+https://github.com/resemble-ai/chatterbox.git@5de7a54aa4e5e2baadb0182dde554908b48b85c2"
& '.\Abrir Voz Local.cmd'
```

Escolha uma gravação sua com pelo menos cinco segundos de fala, digite ou importe o roteiro e clique em **Gerar / retomar**. Para pausar, use **Pausar após este trecho**. A saída padrão é `renders/narracao.wav`. Na primeira execução, os modelos são baixados para `data/`; as gerações seguintes rodam localmente. O motor brasileiro também baixa código do [demo oficial](https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-pt-br) em uma revisão fixa.

O motor prepara a referência de voz uma vez por execução e a reutiliza entre trechos. A geração usa a GPU quando o PyTorch ROCm/CUDA a disponibiliza. A verificação da fala usa CPU para não disputar memória de vídeo com o sintetizador.

Na máquina de desenvolvimento (Radeon RX 9070 XT, Ryzen 5 7600), um roteiro de **196 palavras** produziu **73,16 segundos** de áudio em **166,74 segundos** de processamento com o Multilingual V3. Os nove trechos passaram pela conferência de fala. O teste anterior, com trechos menores e preparação repetida da referência, levou 332,08 segundos para o mesmo roteiro. Os tempos incluem o carregamento do modelo e variam conforme a máquina e o texto.

Se um trecho falhar repetidamente, o aplicativo o divide em partes menores e tenta novamente. Os arquivos já válidos são reutilizados ao retomar uma geração.

## Licenças e créditos

Este projeto foi desenvolvido a partir do estudo e da adaptação de [VoiceStudio](https://github.com/debpalash/VoiceStudio), de Palash Debnath e colaboradores. O código deste repositório é distribuído sob **AGPL-3.0-only**; veja [LICENSE](LICENSE) e [NOTICE.md](NOTICE.md). A implementação de divisão de texto aqui é independente, mas o restante do aplicativo foi inicialmente criado no contexto do VoiceStudio. Preserve os avisos e disponibilize o código fonte das modificações ao distribuir ou oferecer a aplicação pela rede, conforme a licença.

O [Chatterbox](https://github.com/resemble-ai/chatterbox) e os [pesos pt-BR](https://huggingface.co/ResembleAI/Chatterbox-Multilingual-pt-br) declaram licença MIT. Dependências e modelos baixados mantêm suas próprias licenças; este repositório não redistribui pesos ou pacotes. O código do demo pt-BR é baixado somente no primeiro uso, e sua licença deve ser conferida na fonte antes de redistribuí-lo. [OmniVoice](https://github.com/k2-fsa/OmniVoice) foi avaliado, mas não integra esta aplicação; seus pesos publicados têm restrição não comercial.

Use somente gravações que você tem direito de usar. `data/` e `renders/` são ignorados pelo Git, incluindo referências, configurações e arquivos de saída. Antes de publicar alterações, confira também arquivos adicionados manualmente.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_pipeline.py
```

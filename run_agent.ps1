param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $AgentArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root ".venv\Scripts\Activate.ps1")
$env:DEEPSEEK_REASONER_THINKING = "disabled"
if (-not $env:DEEPSEEK_REASONER_EFFORT) { $env:DEEPSEEK_REASONER_EFFORT = "low" }
if (-not $env:DEEPSEEK_REASONER_MAX_TOKENS) { $env:DEEPSEEK_REASONER_MAX_TOKENS = "512" }
if (-not $env:QWEN_VISION_ENABLE_THINKING) { $env:QWEN_VISION_ENABLE_THINKING = "false" }
if (-not $env:QWEN_VISION_MAX_TOKENS) { $env:QWEN_VISION_MAX_TOKENS = "256" }
if (-not $env:OMNIPARSER_HOST) { $env:OMNIPARSER_HOST = "127.0.0.1" }
if (-not $env:OMNIPARSER_PORT) { $env:OMNIPARSER_PORT = "8001" }
if (-not $env:OMNIPARSER_TIMEOUT_SECONDS) { $env:OMNIPARSER_TIMEOUT_SECONDS = "300" }
if (-not $env:OMNIPARSER_BOX_THRESHOLD) { $env:OMNIPARSER_BOX_THRESHOLD = "0.05" }
if (-not $env:OMNIPARSER_IOU_THRESHOLD) { $env:OMNIPARSER_IOU_THRESHOLD = "0.7" }
if (-not $env:OMNIPARSER_USE_PADDLEOCR) { $env:OMNIPARSER_USE_PADDLEOCR = "true" }
if (-not $env:OMNIPARSER_IMGSZ) { $env:OMNIPARSER_IMGSZ = "640" }
python (Join-Path $Root "run_agent.py") @AgentArgs
exit $LASTEXITCODE

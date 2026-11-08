#!/usr/bin/env bash
# /finish check for: extract the duplicated tax rounding in AdrInvoiceTotals into a private roundedTax method
set -u
cd "$(git rev-parse --show-toplevel)"
MODEL_DIR="Metadata/AdroidCore"
CLS="AdrInvoiceTotals"
LINT="python3 .loop/xpplint.py"
step() { out="$("$@" 2>&1)"; [ $? -eq 0 ] || { printf '%s\n' "$out" | tail -n 3; exit 1; }; }

# 1. build: the real X++ compiler when this host has it (D365FO dev VM), else static lint
if command -v msbuild.exe >/dev/null 2>&1 && ls Projects/*/*.rnrproj >/dev/null 2>&1; then
  for p in Projects/*/*.rnrproj; do
    out="$(msbuild.exe "$p" /nologo /v:m /p:Configuration=Debug 2>&1)" || {
      e="$(printf '%s\n' "$out" | grep -m1 ': error ')"
      echo "expected: $p builds with 0 errors"; echo "actual:   ${e#*: error }"; echo "where:    ${e%%: error*}"; exit 1; }
  done
fi
step $LINT lint "$MODEL_DIR"

# 2. task assertions
step $LINT has-method "$MODEL_DIR" $CLS roundedTax --access private
step $LINT count      "$MODEL_DIR" $CLS '/\s*100\s*,\s*0\.01\s*\)' eq 1          # rounding lives in one place
step $LINT count      "$MODEL_DIR" $CLS '\bround\s*\(' eq 1                     # ...and only roundedTax rounds
step $LINT calls      "$MODEL_DIR" $CLS calcLineTax roundedTax
step $LINT calls      "$MODEL_DIR" $CLS calcInvoiceTax roundedTax
step $LINT has-method "$MODEL_DIR" $CLS calcLineTax --access public          # public API unchanged
step $LINT has-method "$MODEL_DIR" $CLS calcInvoiceTax --access public
echo "PASS: static X++ lint (no MSBuild on this host) + 7 task assertions"

#!/usr/bin/env python3
"""Builds the single-page demo (index.html and the artifact body app-body.html) from the server's
client code plus an in-browser stand-in for the API. Run after changing server/public or demo-src."""
from pathlib import Path

root = Path(__file__).parent
read = lambda p: (root / p).read_text(encoding='utf-8')

body = f'''<title>Kesher</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700&family=Frank+Ruhl+Libre:wght@500;700&display=swap">
<style>
{read('server/public/style.css')}
</style>
<div class="app" dir="rtl" lang="he">
  <header class="top" id="top"></header>
  <main id="view"><div class="loading" aria-label="טוען"></div></main>
</div>
<nav class="tabs" id="tabs-nav" dir="rtl" aria-label="ניווט ראשי" hidden><div class="inner" id="tabs"></div></nav>
<div id="layer" dir="rtl"></div>
<script>
{read('demo-src/sample-data.js')}
{read('demo-src/mock-api.js')}
{read('server/public/app.js')}
</script>
'''
(root / 'app-body.html').write_text(body, encoding='utf-8')
(root / 'index.html').write_text(
    '<!doctype html>\n<html lang="he" dir="rtl">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    '<style>[hidden]{display:none!important}body{margin:0}img{max-width:100%}:root{padding-top:env(safe-area-inset-top,0px)}</style>\n'
    '</head>\n<body>\n' + body + '</body>\n</html>\n', encoding='utf-8')
print('built app-body.html and index.html')

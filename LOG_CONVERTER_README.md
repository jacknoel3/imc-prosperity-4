# Log Converter

Converti i tuoi file log JSON di IMC Prosperity 4 in formato CSV.

## Funzionalità

- ✅ Converte log JSON in CSV strutturato
- ✅ Gestisce automaticamente prezzi, volumi, posizioni e PnL
- ✅ Aggrega dati di trade per timestamp
- ✅ Supporta entrambi i prodotti: ASH_COATED_OSMIUM e INTARIAN_PEPPER_ROOT
- ✅ Output lossless e leggibile

## Installazione

### Prerequisiti
- Python 3.8 o superiore
- pip

### Opzione 1: Clone e installa localmente

```bash
git clone https://github.com/yourusername/log-converter.git
cd log-converter

# Installa le dipendenze
pip install pandas
```

### Opzione 2: Installa come package (dopo publish su PyPI)

```bash
pip install imc-log-converter
```

## Uso

### Metodo 1: Terminal (Windows, Mac, Linux)

```bash
# Metodo 1a: Con cd nella cartella
cd log-converter
python -m log_converter your_session.log

# Metodo 1b: Da qualsiasi cartella (dopo pip install)
log-converter your_session.log
```

### Metodo 2: VS Code (Mac/Linux)

1. Apri il repo in VS Code
2. Apri un file `.log` (oppure assicurati sia il file attivo)
3. Premi `Cmd+Shift+B` (Mac) o `Ctrl+Shift+B` (Linux)
4. Seleziona "Convert Log to CSV"
5. ✅ Il CSV verrà salvato nella stessa cartella con suffisso `_lossless.csv`

## Input/Output

### Input
```
your_session.log  (JSON file)
```

### Output
```
your_session_lossless.csv  (CSV con tutte le colonne di mercato)
```

### Struttura CSV

Il file CSV conterrà:
- `timestamp`: Orario dell'evento
- `ash_coated_osmium_*`: Dati mercato ASH (bid/ask/volume/mid/spread/pnl)
- `intarian_pepper_root_*`: Dati mercato IPR (bid/ask/volume/mid/spread/pnl)
- `*_trade_price`, `*_trade_qty`, `*_trade_side`: Dettagli trade eseguiti
- `algo_log`: Colonna di log aggiuntiva (vuota per ora)

## Esempio Completo

```bash
# 1. Clone il repo
git clone https://github.com/yourusername/log-converter.git
cd log-converter

# 2. Installa dipendenze
pip install pandas

# 3. Scarica il tuo file log da IMC Prosperity
# (salva come: session_round2_day1.log)

# 4. Converti in CSV
python -m log_converter session_round2_day1.log

# 5. Apri il CSV in Excel/Pandas/etc
# session_round2_day1_lossless.csv ✅
```

## Troubleshooting

### "File non trovato"
```bash
# Assicurati che il file esista e sia nel percorso corretto
ls -la your_session.log
```

### "activitiesLog vuoto"
Il file JSON non contiene il campo `activitiesLog`. Verifica che sia un log valido di IMC Prosperity.

### "ModuleNotFoundError: pandas"
```bash
# Installa pandas
pip install pandas
```

### Task non appare in VS Code
1. Assicurati di essere nella cartella del repo
2. Ricarica VS Code (`Cmd+R`)
3. Apri un file `.log`
4. Prova `Cmd+Shift+B`

## Struttura Repo

```
log-converter/
├── log_converter/
│   ├── __init__.py         # Package initialization
│   ├── __main__.py         # CLI entry point
│   └── converter.py        # Core conversion logic
├── .vscode/
│   └── tasks.json          # VS Code build task
├── pyproject.toml          # Package metadata
├── README.md               # This file
├── LICENSE                 # MIT License
└── .gitignore              # Git ignore rules
```

## Sviluppo

### Per estendere il converter

Modifica `log_converter/converter.py`:
- Aggiungi nuovi prodotti nell'array `products`
- Personalizza la mappatura delle colonne
- Estendi la logica di aggregazione trade

### Per testare localmente

```bash
# Copia un file .log di test nella cartella
cp ~/Downloads/session_123.log .

# Esegui la conversione
python -m log_converter session_123.log

# Verifica l'output
head session_123_lossless.csv
```

## Contributi

Per reportare bug o suggerire feature:
1. Apri una Issue su GitHub
2. Oppure fai una Pull Request

## License

MIT License - vedi il file [LICENSE](LICENSE) per i dettagli.

## Support

Per domande o problemi:
- 📧 Email: your.email@example.com
- 🐛 Issues: https://github.com/yourusername/log-converter/issues

#!/usr/bin/env bash
# Descarga los datos públicos que usa el motor a data/ (no se versionan).
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p data
base=https://ecobici.cdmx.gob.mx/wp-content/uploads
declare -A viajes=(
  [viajes_2026-07.csv]=$base/2026/08/public_data_web_2026-07.csv
  [viajes_2026-08.csv]=$base/2026/09/public_data_web_2026-08_2.csv
  [viajes_2026-09.csv]=$base/2026/10/public_data_web_2026-09.csv
)
for f in "${!viajes[@]}"; do
  [ -s "data/$f" ] && { echo "ya existe data/$f"; continue; }
  echo "descargando $f…"; curl -fL --retry 3 -A "Mozilla/5.0" -o "data/$f" "${viajes[$f]}"
done
for f in station_information station_status; do
  curl -fsL --retry 3 -o "data/$f.json" "https://gbfs.mex.lyftbikes.com/gbfs/es/$f.json"
done
echo "listo: $(ls data | wc -l) archivos en data/"

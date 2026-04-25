---
name: python-packages
description: Installing and using common Python packages in SkillBench containers. Covers scientific computing, data analysis, and file format libraries.
---

# Python Packages Skill

## Installation Pattern
Containers run as root. Always use `--break-system-packages`:
```bash
pip3 install --break-system-packages <package>
```

## Common Packages by Category

### Data Analysis
```bash
pip3 install --break-system-packages pandas numpy openpyxl xlsxwriter
```
- `pandas`: DataFrames, CSV/Excel/JSON I/O
- `numpy`: Numerical arrays, linear algebra, statistics
- `openpyxl`: Read/write Excel .xlsx files
- `xlsxwriter`: Write Excel with formatting

### Scientific Computing
```bash
pip3 install --break-system-packages scipy scikit-learn matplotlib
```
- `scipy`: Optimization, interpolation, signal processing, statistics
- `scikit-learn`: ML models, preprocessing, metrics
- `matplotlib`: Plotting (use `Agg` backend: `import matplotlib; matplotlib.use('Agg')`)

### File Formats
```bash
pip3 install --break-system-packages python-pptx python-docx Pillow PyPDF2 pyyaml
```
- `python-pptx`: PowerPoint files
- `python-docx`: Word documents
- `Pillow`: Image processing
- `PyPDF2` or `pypdf`: PDF reading
- `pyyaml`: YAML parsing

### Web & APIs
```bash
pip3 install --break-system-packages requests beautifulsoup4 lxml
```

### Geospatial & Domain-Specific
```bash
pip3 install --break-system-packages shapely geopandas  # GIS
pip3 install --break-system-packages obspy              # Seismology
pip3 install --break-system-packages rdkit-pypi          # Chemistry (may need conda)
pip3 install --break-system-packages astropy             # Astronomy
```

## Pre-installed Check
Before installing, check what's available:
```bash
pip3 list 2>/dev/null | head -40
python3 -c "import pandas; print(pandas.__version__)" 2>/dev/null
```

## Common Issues
- **matplotlib backend**: Set `matplotlib.use('Agg')` before importing pyplot
- **Missing C libraries**: `apt-get update && apt-get install -y libgdal-dev` for geopandas
- **Compilation**: Some packages need `gcc`: `apt-get install -y build-essential`
- **Timeout**: Large packages (torch, tensorflow) may timeout — check if pre-installed first

---
name: scientific-computing
description: Strategies for scientific computing tasks including data analysis, numerical methods, and ML model tasks.
---

# Scientific Computing Skill

For tasks involving data science, ML, numerical methods, and scientific analysis.

## 1. Check available libraries
```bash
pip list 2>/dev/null | grep -iE "numpy|scipy|pandas|torch|tensorflow|sklearn"
R -e "installed.packages()[,'Package']" 2>/dev/null | head -20
```

## 2. Data inspection
```bash
# Check file formats
file /app/data* 2>/dev/null
head -5 /app/*.csv 2>/dev/null
python3 -c "import json; print(json.load(open('/app/data.json'))[:2])" 2>/dev/null
```

## 3. Common patterns
```bash
# Install missing Python packages
pip install numpy scipy pandas matplotlib scikit-learn 2>/dev/null

# Install R packages
R -e "install.packages(c('ggplot2','dplyr'), repos='https://cloud.r-project.org')" 2>/dev/null
```

## Tips
- Always verify output format matches what the task specifies
- Save results to the exact paths mentioned in the task prompt
- Check for numerical precision requirements

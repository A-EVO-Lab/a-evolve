---
name: build-compiled-extensions
description: How to build C/C++/Cython/Fortran extensions and frameworks like Caffe, OpenCV, protobuf-dependent projects from source.
---

# Building Compiled Extensions & Frameworks

## Pre-flight
```bash
pip install setuptools wheel cython 2>/dev/null
apt-get install -y build-essential python3-dev 2>/dev/null
```

## Cython extension workflow
```bash
cython file.pyx   # Cythonize to .c first
python setup.py build_ext --inplace 2>&1
# Manual fallback:
gcc -shared -fPIC -O2 $(python3-config --includes) -o mod$(python3-config --extension-suffix) mod.c
```

## Building large C++ frameworks (Caffe, etc.)
1. Check protobuf version: `protoc --version` and `dpkg -l | grep protobuf`
2. Fix API incompatibilities before building (see debug-and-fix skill)
3. Use `make -j$(nproc)` but capture errors: `make 2>&1 | tee build.log`
4. For Caffe specifically:
   - Set `CPU_ONLY := 1` in Makefile.config for non-GPU environments
   - Fix protobuf SetTotalBytesLimit calls for newer protobuf
   - Use `make all -j$(nproc) && make test && make runtest`

## When pip install times out
1. Install dependencies individually first: `pip install numpy scipy`
2. Build extensions only: `python setup.py build_ext --inplace`
3. Or: `pip install --no-deps -e .`

## Common pitfalls
- **Missing numpy headers**: `pip install numpy` before building numpy-dependent C extensions
- **Import from wrong dir**: Test from `/tmp`, not the source directory
- **Protobuf mismatch**: Check version and fix API calls before compiling

## Verification
```bash
find . -name "*.so" -newer setup.py
cd /tmp && python -c "import mypackage; print('OK')"
```

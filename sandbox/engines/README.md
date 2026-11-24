# Engine environments, pinned

The packages of each engine's Python environment as they run on Studio's first machine (RX 7900 XTX, Python 3.12,
ROCm 7.2 wheels), used by the installer (`installer.py`, Account & settings → Tools & setup) with `pip install --no-deps`,
so a new machine gets exactly these versions:

- `comfyui.txt`: ComfyUI's own environment, PyTorch 2.14.1 for ROCm 7.2 among them (from `https://download.pytorch.org/whl/rocm7.2`).
- `voice.txt`, `sound.txt`, `music.txt`: the voices, sound-effect and HeartMuLa environments. They hold only their own
  packages; PyTorch and the rest come from ComfyUI's environment through `zz-comfyui-torch.pth`, so they must use the
  same Python as ComfyUI. MOSS-SoundEffect and heartlib are installed from their Git commits (`MOSS_GIT`, `HEARTLIB_GIT`).

Made with `pip freeze` (editable lines removed). After updating an engine on purpose, write its file again the same way
and check that the installer's tests still pass.

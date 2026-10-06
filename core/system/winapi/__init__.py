"""
core/system/winapi/ — the Windows mechanisms the abilities stand on.

Every subprocess in this package is a FIXED argument vector with
`shell=False`; every COM call is a typed vtable slot. Nothing here takes a
string it would execute. The only variables that reach Windows are closed
enum values validated in Python first (radio kind, on/off) and bounded
numbers (a volume scalar). CLAUDE.md invariant 4, held at the last mile.

    audio.py    IAudioEndpointVolume over stdlib ctypes — get/set/mute, ~10 ms
    radios.py   Windows.Devices.Radios via PowerShell 5.1 + radios.ps1 — state and toggle
    wlan.py     `netsh wlan show interfaces`, parsed — connected SSID, signal, band
"""

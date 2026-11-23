"""Identidade deste computador, a mesma depois de formatar o sistema.

Vem da placa-mãe (UUID do SMBIOS), que a formatação não muda. Sem UUID válido (placas que
trazem zeros ou o valor de fábrica) cai para o identificador do sistema, que muda ao formatar.
"""
import uuid

from . import config

_INVALIDOS = {
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "03000200-0400-0500-0006-000700080009",   # valor de fábrica de placas AMI
    "12345678-1234-5678-1234-567812345678",
}


def _valido(u):
    return bool(u) and str(u).lower() not in _INVALIDOS


def _smbios_windows():
    import ctypes
    k = ctypes.windll.kernel32
    rsmb = 0x52534D42
    n = k.GetSystemFirmwareTable(rsmb, 0, None, 0)
    if n <= 8:
        return None
    buf = ctypes.create_string_buffer(n)
    if not k.GetSystemFirmwareTable(rsmb, 0, buf, n):
        return None
    cru = buf.raw
    dados = cru[8:8 + int.from_bytes(cru[4:8], "little")]
    i = 0
    while i + 4 <= len(dados):
        tipo, tam = dados[i], dados[i + 1]
        if tipo == 1 and tam >= 0x19:               # Informações do sistema: UUID nos bytes 8..23
            return str(uuid.UUID(bytes_le=bytes(dados[i + 8:i + 24])))
        if tipo == 127 or tam < 4:
            break
        j = i + tam
        while j + 1 < len(dados) and not (dados[j] == 0 and dados[j + 1] == 0):
            j += 1                                   # pula os textos do registro (acabam em dois zeros)
        i = j + 2
    return None


def _guid_windows():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
        return str(winreg.QueryValueEx(k, "MachineGuid")[0])


def _ler(caminho):
    try:
        with open(caminho, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def identidade():
    """Texto estável deste computador, ou "" se não deu para descobrir."""
    try:
        if config.WIN:
            u = _smbios_windows()
            if _valido(u):
                return "smbios:" + u.lower()
            g = _guid_windows()
            return "sistema:" + g.lower() if g else ""
        u = _ler("/sys/class/dmi/id/product_uuid")
        if _valido(u):
            return "smbios:" + u.lower()
        m = _ler("/etc/machine-id") or _ler("/var/lib/dbus/machine-id")
        return "sistema:" + m.lower() if m else ""
    except Exception:  # noqa: BLE001  (sem permissão ou sistema sem SMBIOS: o chamador sorteia um ID)
        return ""

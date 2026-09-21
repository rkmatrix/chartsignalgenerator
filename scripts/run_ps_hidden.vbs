' Run a PowerShell script fully hidden (no console flash).
' Usage from Task Scheduler:
'   Program: wscript.exe
'   Arguments: "C:\Projects\trading\TradeHub\run_ps_hidden.vbs" "C:\path\to\script.ps1" [extra args...]
Option Explicit
Dim sh, i, ps, cmd, arg
Set sh = CreateObject("WScript.Shell")
If WScript.Arguments.Count < 1 Then
  WScript.Quit 1
End If
ps = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
cmd = """" & ps & """ -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & WScript.Arguments(0) & """"
For i = 1 To WScript.Arguments.Count - 1
  arg = WScript.Arguments(i)
  If InStr(arg, " ") > 0 Then
    cmd = cmd & " """ & arg & """"
  Else
    cmd = cmd & " " & arg
  End If
Next
' 0 = hidden window, False = do not wait
sh.Run cmd, 0, False

Option Explicit

Dim sh, fso, scriptDir, ps1, cmd

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = scriptDir & "\launch_streamlit_hidden.ps1"

If Not fso.FileExists(ps1) Then
    MsgBox "Missing launcher:" & vbCrLf & ps1, vbCritical, "短线看板"
    WScript.Quit 1
End If

sh.CurrentDirectory = scriptDir

cmd = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """"
sh.Run cmd, 0, False

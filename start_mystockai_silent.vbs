Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")

ScriptDir = Fso.GetParentFolderName(WScript.ScriptFullName)
BatPath = Chr(34) & ScriptDir & "\start_mystockai.bat" & Chr(34)

' 0 = hidden window, False = do not wait
WshShell.Run "cmd /c " & BatPath, 0, False

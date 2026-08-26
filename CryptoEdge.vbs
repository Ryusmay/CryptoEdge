Option Explicit

Dim shell, fileSystem, root, launcher, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
root = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = fileSystem.BuildPath(root, "launch_tauri.ps1")

If Not fileSystem.FileExists(launcher) Then
    MsgBox "Brak launch_tauri.ps1.", 16, "CryptoEdge"
    WScript.Quit 1
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & launcher & """"
exitCode = shell.Run(command, 0, True)
If exitCode <> 0 Then
    MsgBox "Nie udało się uruchomić natywnego interfejsu CryptoEdge. Szczegóły: logs\launcher_status.log", 16, "CryptoEdge"
End If
WScript.Quit exitCode

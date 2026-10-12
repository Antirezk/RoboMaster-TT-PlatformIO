Option Explicit
Dim shell, fs, entry
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
entry = fs.BuildPath(fs.GetParentFolderName(WScript.ScriptFullName), "TelloControl.pyw")
On Error Resume Next
shell.Run "pythonw.exe """ & entry & """", 1, False
If Err.Number <> 0 Then
    MsgBox "Cannot launch Python. Open TelloControl.pyw with pythonw.exe.", 16, "TT Voice Console"
End If

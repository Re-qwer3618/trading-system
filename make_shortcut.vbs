' Double-click this file once to create a desktop shortcut.
' After that, just double-click the desktop icon.

Set oWS = WScript.CreateObject("WScript.Shell")
strPath = oWS.CurrentDirectory
sLinkFile = oWS.SpecialFolders("Desktop") & "\Trading Dashboard.lnk"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = strPath & "\start_dashboard.bat"
oLink.WorkingDirectory = strPath
oLink.WindowStyle = 1
oLink.IconLocation = "%SystemRoot%\System32\SHELL32.dll, 13"
oLink.Description = "Start the trading dashboard"
oLink.Save

MsgBox "Desktop shortcut 'Trading Dashboard' created." & vbCrLf & "From now on, just double-click that icon.", vbInformation, "Done"

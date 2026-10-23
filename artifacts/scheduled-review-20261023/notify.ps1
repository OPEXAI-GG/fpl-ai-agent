param(
    [string]$Title = 'FPL deadline review',
    [string]$Message = 'Open Codex to review your team. No account changes were made.'
)
$ErrorActionPreference = 'Stop'
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $document = New-Object Windows.Data.Xml.Dom.XmlDocument
    $safeTitle = [System.Security.SecurityElement]::Escape($Title)
    $safeMessage = [System.Security.SecurityElement]::Escape($Message)
    $document.LoadXml('<toast><visual><binding template="ToastGeneric"><text>' + $safeTitle + '</text><text>' + $safeMessage + '</text></binding></visual></toast>')
    $toast = [Windows.UI.Notifications.ToastNotification]::new($document)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Windows PowerShell').Show($toast)
    Write-Output 'Desktop notification submitted; receipt depends on Windows notification settings.'
} catch {
    $popup = New-Object -ComObject WScript.Shell
    $null = $popup.Popup($Message, 20, $Title, 48)
    Write-Output 'Desktop popup requested.'
}

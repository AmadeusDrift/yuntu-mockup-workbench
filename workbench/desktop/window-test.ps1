param([int]$OwnerPid,[string]$ExpectedExe,[string]$Action='show')
$ErrorActionPreference='Stop'
$taskProcess=Get-Process -Id $OwnerPid
if($taskProcess.Path -ne $ExpectedExe){throw 'Test process path mismatch'}
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class MockupScriptWindowTest {
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h,int c);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
}
'@
if($Action -eq 'show') {
 [MockupScriptWindowTest]::ShowWindowAsync($taskProcess.MainWindowHandle,9) | Out-Null
 [MockupScriptWindowTest]::SetForegroundWindow($taskProcess.MainWindowHandle) | Out-Null
} elseif($Action -eq 'close') {
 [MockupScriptWindowTest]::PostMessage($taskProcess.MainWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero) | Out-Null
} else {throw 'Unknown test action'}

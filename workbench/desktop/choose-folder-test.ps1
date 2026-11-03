param([int]$OwnerPid,[string]$Folder,[switch]$CancelAll)
$ErrorActionPreference='Stop'
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class NativeFolderScriptTest {
 public delegate bool Callback(IntPtr h,IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumWindows(Callback c,IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p,Callback c,IntPtr l);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder t,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,StringBuilder t,int n);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
 [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageW")] public static extern IntPtr Text(IntPtr h,uint m,IntPtr w,string s);
 [DllImport("user32.dll",EntryPoint="PostMessageW")] public static extern bool Message(IntPtr h,uint m,IntPtr w,IntPtr l);
 public static IntPtr[] Dialogs(int owner,string title){var list=new List<IntPtr>();EnumWindows((h,l)=>{
  uint p;GetWindowThreadProcessId(h,out p);if(p!=owner)return true;
  var t=new StringBuilder(256);var c=new StringBuilder(256);GetWindowText(h,t,256);GetClassName(h,c,256);
  if(c.ToString()=="#32770"&&t.ToString()==title&&IsWindowVisible(h))list.Add(h);return true;
 },IntPtr.Zero);return list.ToArray();}
 public static IntPtr Child(IntPtr parent,int id,string classname){IntPtr result=IntPtr.Zero;EnumChildWindows(parent,(h,l)=>{
  var c=new StringBuilder(256);GetClassName(h,c,256);if(GetDlgCtrlID(h)==id&&c.ToString()==classname){result=h;return false;}return true;
 },IntPtr.Zero);return result;}
}
"@
$taskTitle=([char[]](0x9009,0x62e9,0x6587,0x4ef6,0x5939))-join ''
if($CancelAll){foreach($taskDialog in [NativeFolderScriptTest]::Dialogs($OwnerPid,$taskTitle)){[NativeFolderScriptTest]::Message($taskDialog,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null};exit 0}
$taskSelected=[IntPtr]::Zero
for($taskAttempt=0;$taskAttempt -lt 100;$taskAttempt++){
 foreach($taskDialog in [NativeFolderScriptTest]::Dialogs($OwnerPid,$taskTitle)){if([NativeFolderScriptTest]::IsWindowEnabled($taskDialog)){$taskSelected=$taskDialog;break}}
 if($taskSelected -ne [IntPtr]::Zero){break};Start-Sleep -Milliseconds 100
}
if($taskSelected -eq [IntPtr]::Zero){throw 'Native folder dialog did not appear'}
$taskEdit=[NativeFolderScriptTest]::Child($taskSelected,1152,'Edit')
$taskButton=[NativeFolderScriptTest]::Child($taskSelected,1,'Button')
if($taskEdit -eq [IntPtr]::Zero -or $taskButton -eq [IntPtr]::Zero){throw 'Native folder controls missing'}
[NativeFolderScriptTest]::Text($taskEdit,0xC,[IntPtr]::Zero,$Folder)|Out-Null
[NativeFolderScriptTest]::Message($taskButton,0xF5,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null
Write-Output 'Folder chosen through native dialog'

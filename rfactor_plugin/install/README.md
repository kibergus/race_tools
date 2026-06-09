I want an installer/configurator GUI for the plugin.

It should be a standalone application. I want the plugin files to be bundled inside of this application, so that there is just one file to download.

Upon start it looks for the steam installation dirs and locates the rfactor installation.
It checks whether the plugin is already installed and whether it is of the same version as the current plugin. If not, it asks whether to install it and whether to activate the plugin (make it active in rFactor). It also should ask for the API key and whether to save files to the drive (default folder to somewhere in user folder) then create a config.

After that or if teh plugin is already installed it enters the configuration phase. It provides a GUI to set all the stuff in the config JSON.

There also should be a button to uninstall the plugin. maybe put it ona separate page where later we can add an auto-update feature. 


====

The installer caprtion should be just "BrBrDb Telemetry"

Remove "H-F Telemetry" label

"Browse" buttons are unproportionally tall, 3x the input size.

If rfactor installation dir can not be determined automatically, just show an error about that in the UI. Installation is not possible in this case.

If the plugin is not installed, the install tab should be the only one visible tab. "Remote Dashboard API key"  should be just "API Key" and it must be required. "Save telemetry files to folder" should default to <Documents>/BrBrDbTelemetry where <Documents> is the Documents folder of the user. 

If the plugin is outdated show the update tab (it should be the only tab available). It should say "Update BrBrDbTelemetry to version X.Y.Z?" and a button to do so.

Rename "About & uninstall" to "Manage installation". Add there a button to re-install the plugin.
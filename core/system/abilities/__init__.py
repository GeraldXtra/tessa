"""
core/system/abilities/ — one module per capability group. Discovered by
`core.system.capability.discover()`; each module exports `CAPABILITIES`.

    volume.py       system.volume.get / .set / .mute      green   system.control
    brightness.py   system.brightness.get / .set          green   system.control
    network.py      system.network.status                 green   system.status
    radio.py        system.radio.set                      amber   system.radio
    app.py          system.app.launch                     green   app.launch
    process.py      system.process.details                green   system.status
                    system.process.priority               amber   process.priority
                    (proc.list/top/find/kill stay in core/tools/procs.py; kill is
                     pid-frozen and shares its refuse list with priority)
    power.py        system.power.shutdown / .restart      red     system.shutdown
                    system.power.hibernate / .logoff      red     system.shutdown
                      (approval SCHEDULES it, 60 s by default, 10 s-1 h if he
                       names a time; cancellable until it fires; no /f)
                    system.power.cancel                   green   system.control
                    system.power.status                   green   system.status
                    (sleep and lock stay sys.sleep / sys.lock in core/tools/sysctl.py)

A module here declares facts and an implementation. It never checks a tier,
never asks for confirmation, never reads an approval flag — the binding in
core/system/capability.py does all of that from permissions.yaml, so it
cannot be forgotten. A module that needs a NEW permissions.yaml key adds
exactly one line there (radio.py did: `system.radio`, amber).
"""

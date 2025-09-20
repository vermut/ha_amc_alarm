AMC Alarm integration for HomeAssistant (davideciarmiello)
===

      This has no official affiliation with AMC. Henceforth, it does not offer any guarantees, liability or promises that 
      it works.

Usage
===

* Install it via HACS by adding custom repository
* Add new integration "AMC Alarm"
* Fill in your login and central credentials
* Zones, groups and areas are alarm panels.
* Notification list is in attributes of a sensor.
* Tamper system alerts are binary sensors.
* Outputs are present.
      
Compatibility
===

Known to work on:
* K8/1.77
* X824/3.73
* X824V/4.10
* X864V/4.10
* X64V/4.20


Presumable would be compatibly with anything that uses AMC Plus app. If it works for you - drop me a note.


## Thanks
The API code is mostly derived from [DIA Chacon API](https://github.com/cnico/dio-chacon-wifi-api). Hass integration and supporting GitHub/HACS manifests are stolen from [HA Toyota](https://github.com/DurgNomis-drol/ha_toyota). Thank you, guys! 

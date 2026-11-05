# Portfolio media: Smart Toll Plaza System

Recorded from the running application in Google Chrome, with a script driving the mouse and keyboard.

| Folder | Contents |
|---|---|
| `demo/` | The full walkthrough video, with title cards |
| `clips/` | Five short clips, one per feature |
| `screenshots/` | 23 screenshots at 1920x1080, numbered in showcase order |

## What is real and what is simulated

**Real:** the application, the plate detection and OCR (they run live on the video stream), the WebRTC stream, the rules engine, the database writes, the role-based access, and the map of 2,003 plazas from the registry CSV.

**Simulated:**
- The camera feed is generated footage of drawn cars, fed to Chrome as a virtual webcam. It is not real traffic.
- The analytics history (about 990 transactions over 14 days) is generated demo data.
- FASTag payment is a mock, as in the app itself.

Clips that use simulated footage or data carry a corner label. Please keep those labels, and describe this as a simulation or prototype. It is not an official NHAI system.

## Suggested portfolio caption

> A full-stack simulation of a national toll operations platform. Number plates are read live from a video stream using YOLOv9 detection and CCT OCR, checked against a blacklist and exemption rules, and charged with congestion-based pricing. It includes a Pan-India GIS map of 2,003 toll plazas and role-based finance and security dashboards.

# MPC-BE Web Interface API

This document describes the MPC-BE web interface exposed by the local player instance running on `http://127.0.0.1:13579`.

This version is based on the actual MPC-BE source from GitHub, not just live UI inspection.

## Authoritative upstream sources

The command list and web behavior below come from these MPC-BE source files:

- `src/apps/mplayerc/resource.h`
  - authoritative numeric command IDs
- `src/apps/mplayerc/WebClient.cpp`
  - `command.html` behavior and special web-only commands
- `src/apps/mplayerc/WebServer.cpp`
  - endpoint table and built-in asset deployment
- `src/apps/mplayerc/WebClient.h`
  - request handler declarations

Upstream repository:

- https://github.com/Aleksoid1978/MPC-BE

Relevant source paths:

- https://github.com/Aleksoid1978/MPC-BE/blob/master/src/apps/mplayerc/resource.h
- https://github.com/Aleksoid1978/MPC-BE/blob/master/src/apps/mplayerc/WebClient.cpp
- https://github.com/Aleksoid1978/MPC-BE/blob/master/src/apps/mplayerc/WebServer.cpp

## Base URL

- `http://127.0.0.1:13579`

## Endpoints

From `WebServer.cpp`, the built-in internal pages are:

- `GET /`
- `GET /index.html`
- `GET /info.html`
- `GET /browser.html`
- `GET /controls.html`
- `GET /command.html`
- `POST /command.html`
- `GET /status.html`
- `GET /player.html`
- `GET /variables.html`
- `GET /snapshot.jpg`
- `GET /404.html`

Built-in downloadable assets exposed by the web server include:

- `GET /default.css`
- `GET /favicon.png`
- `GET /logo.png`
- `GET /seekbarleft.png`
- `GET /seekbarmid.png`
- `GET /seekbarright.png`
- `GET /seekbargrip.png`
- `GET /controlbuttonplay.png`
- `GET /controlbuttonpause.png`
- `GET /controlbuttonstop.png`
- `GET /controlbuttonskipback.png`
- `GET /controlbuttondecrate.png`
- `GET /controlbuttonincrate.png`
- `GET /controlbuttonskipforward.png`
- `GET /controlbuttonstep.png`
- `GET /controlvolumeon.png`
- `GET /controlvolumeoff.png`
- `GET /controlvolumebar.png`
- `GET /controlvolumegrip.png`

The web server can also expose static files and CGI handlers from the configured web root.

## `command.html` behavior

From `WebClient.cpp`, `command.html` reads `wm_command` from the request and behaves as follows:

- If `wm_command == CMD_SETPOS`
  - accepts either `position=HH:MM:SS[.ms]`
  - or `percent=<number>`
- If `wm_command == CMD_SETVOLUME`
  - accepts `volume=<0-100>`
- If `wm_command == ID_FILE_EXIT`
  - posts the exit command asynchronously
- For any other positive `wm_command`
  - forwards it to the player as `WM_COMMAND`

This is the key point: the web API is not limited to a tiny hardcoded list. For any positive command ID defined in `resource.h`, the web server forwards it into the player.

## Special web-only command IDs

These are handled specially by `WebClient.cpp` and are not normal `resource.h` command IDs:

| `wm_command` | Meaning | Parameters |
| --- | --- | --- |
| `-1` | Absolute seek | `position=HH:MM:SS[.ms]` or `percent=<0-100>` |
| `-2` | Absolute volume set | `volume=<0-100>` |

Examples:

```text
/command.html?wm_command=-1&position=01:07:42
/command.html?wm_command=-1&percent=50
/command.html?wm_command=-2&volume=80
```

## Variables exposed by `/variables.html`

From `WebClient.cpp`, `OnVariables()` emits the following fields into the HTML page:

- `file`
- `filepatharg`
- `filepath`
- `filedirarg`
- `filedir`
- `state`
- `statestring`
- `position`
- `positionstring`
- `duration`
- `durationstring`
- `volumelevel`
- `muted`
- `playbackrate`
- `size`
- `reloadtime`
- `hdr`
- `version`

Observed meanings from source:

- `state`: numeric `OAFilterState`
- `statestring`: localized text such as playing, paused, stopped
- `position`: current playback position in milliseconds
- `positionstring`: current playback position formatted as `HH:MM:SS`
- `duration`: total duration in milliseconds
- `durationstring`: total duration formatted as `HH:MM:SS`
- `volumelevel`: toolbar volume control position, usually `0-100`
- `muted`: `1` when muted, `0` otherwise
- `playbackrate`: currently emitted as `1` in this source path
- `size`: formatted file size string
- `reloadtime`: currently emitted as `0`
- `hdr`: `SDR`, `HDR`, or `HDR(HLG)` depending on media/render graph inspection
- `version`: full MPC-BE version string

Example shape:

```html
<p id="position">4062466</p>
<p id="positionstring">01:07:42</p>
<p id="duration">8060032</p>
<p id="durationstring">02:14:20</p>
```

## Browser endpoint behavior

From `WebClient.cpp`, `browser.html` supports:

- `path=<filesystem path>`
- optional `focus=no`

When `path` points to a file, the browser page can send a `WM_COPYDATA` message to open that file in MPC-BE.

## Actual command listing from `resource.h`

These are the actual command IDs defined by MPC-BE in the `commands` block of `src/apps/mplayerc/resource.h`.

Because `WebClient.cpp` forwards any positive `wm_command` to `WM_COMMAND`, these are the real actionable web command IDs.

### File commands

| ID | Symbol |
| --- | --- |
| 800 | `ID_FILE_OPENFILEURL` |
| 801 | `ID_FILE_OPENDVD` |
| 802 | `ID_FILE_OPENDEVICE` |
| 803 | `ID_FILE_CLOSEMEDIA` |
| 804 | `ID_FILE_CLOSEPLAYLIST` |
| 805 | `ID_FILE_SAVE_COPY` |
| 806 | `ID_FILE_SAVE_IMAGE` |
| 807 | `ID_FILE_AUTOSAVE_IMAGE` |
| 808 | `ID_FILE_SAVE_THUMBNAILS` |
| 809 | `ID_FILE_LOAD_SUBTITLE` |
| 810 | `ID_FILE_SAVE_SUBTITLE` |
| 812 | `ID_FILE_ISDB_DOWNLOAD` |
| 813 | `ID_FILE_ISDB_SEARCH` |
| 814 | `ID_FILE_PROPERTIES` |
| 816 | `ID_FILE_EXIT` |
| 969 | `ID_FILE_OPENFILE` |
| 976 | `ID_FILE_REOPEN` |
| 996 | `ID_FILE_AUTOSAVE_DISPLAY` |
| 1016 | `ID_FILE_OPENDIRECTORY` |
| 1035 | `ID_FILE_LOAD_AUDIO` |
| 1090 | `ID_FILE_OPENISO` |

### View and window commands

| ID | Symbol |
| --- | --- |
| 815 | `ID_VIEW_OPTIONS` |
| 817 | `ID_VIEW_CAPTIONMENU` |
| 818 | `ID_VIEW_SEEKER` |
| 819 | `ID_VIEW_CONTROLS` |
| 820 | `ID_VIEW_INFORMATION` |
| 821 | `ID_VIEW_STATISTICS` |
| 822 | `ID_VIEW_STATUS` |
| 823 | `ID_VIEW_SUBRESYNC` |
| 824 | `ID_VIEW_PLAYLIST` |
| 825 | `ID_VIEW_CAPTURE` |
| 826 | `ID_VIEW_SHADEREDITOR` |
| 827 | `ID_VIEW_PRESETS_MINIMAL` |
| 828 | `ID_VIEW_PRESETS_COMPACT` |
| 829 | `ID_VIEW_PRESETS_NORMAL` |
| 830 | `ID_VIEW_FULLSCREEN` |
| 831 | `ID_VIEW_FULLSCREEN_2` |
| 832 | `ID_VIEW_ZOOM_50` |
| 833 | `ID_VIEW_ZOOM_100` |
| 834 | `ID_VIEW_ZOOM_200` |
| 835 | `ID_VIEW_VF_HALF` |
| 836 | `ID_VIEW_VF_NORMAL` |
| 837 | `ID_VIEW_VF_DOUBLE` |
| 838 | `ID_VIEW_VF_STRETCH` |
| 839 | `ID_VIEW_VF_FROMINSIDE` |
| 840 | `ID_VIEW_VF_FROMOUTSIDE` |
| 841 | `ID_VIEW_VF_ZOOM1` |
| 842 | `ID_VIEW_VF_ZOOM2` |
| 843 | `ID_VIEW_VF_SWITCHZOOM` |
| 844 | `ID_VIEW_VF_KEEPASPECTRATIO` |
| 845 | `ID_VIEW_VF_COMPMONDESKARDIFF` |
| 859 | `ID_ASPECTRATIO_NEXT` |
| 861 | `ID_VIEW_RESET` |
| 862 | `ID_VIEW_INCSIZE` |
| 863 | `ID_VIEW_DECSIZE` |
| 864 | `ID_VIEW_INCWIDTH` |
| 865 | `ID_VIEW_DECWIDTH` |
| 866 | `ID_VIEW_INCHEIGHT` |
| 867 | `ID_VIEW_DECHEIGHT` |
| 968 | `ID_VIEW_ZOOM_AUTOFIT` |
| 1009 | `ID_VIEW_NAVIGATION` |
| 1023 | `ID_D3DFULLSCREEN_TOGGLE` |
| 1038 | `ID_WINDOW_TO_PRIMARYSCREEN` |
| 1040 | `ID_VIEW_RESETSTATS` |
| 1041 | `ID_VIEW_TEARING_TEST` |
| 1042 | `ID_VIEW_DISPLAYSTATS` |
| 1043 | `ID_VIEW_REMAINING_TIME` |
| 1044 | `ID_VIEW_EVROUTPUTRANGE_0_255` |
| 1045 | `ID_VIEW_EVROUTPUTRANGE_16_235` |
| 1046 | `ID_VIEW_EXCLUSIVE_FULLSCREEN` |
| 1053 | `ID_VIEW_ENABLEFRAMETIMECORRECTION` |
| 1066 | `ID_VIEW_VSYNC` |
| 1067 | `ID_VIEW_VSYNCINTERNAL` |
| 1070 | `ID_VIEW_VSYNCOFFSET_DECREASE` |
| 1071 | `ID_VIEW_VSYNCOFFSET_INCREASE` |
| 1075 | `ID_VIEW_RESET_DEFAULT` |

### Aspect ratio presets

| ID | Symbol |
| --- | --- |
| 850 | `ID_ASPECTRATIO_SOURCE` |
| 851 | `ID_ASPECTRATIO_4_3` |
| 852 | `ID_ASPECTRATIO_5_4` |
| 853 | `ID_ASPECTRATIO_16_9` |
| 854 | `ID_ASPECTRATIO_235_100` |
| 855 | `ID_ASPECTRATIO_185_100` |

### Pan and scan commands

| ID | Symbol |
| --- | --- |
| 868 | `ID_PANSCAN_MOVELEFT` |
| 869 | `ID_PANSCAN_MOVERIGHT` |
| 870 | `ID_PANSCAN_MOVEUP` |
| 871 | `ID_PANSCAN_MOVEDOWN` |
| 872 | `ID_PANSCAN_MOVEUPLEFT` |
| 873 | `ID_PANSCAN_MOVEUPRIGHT` |
| 874 | `ID_PANSCAN_MOVEDOWNLEFT` |
| 875 | `ID_PANSCAN_MOVEDOWNRIGHT` |
| 876 | `ID_PANSCAN_CENTER` |
| 880 | `ID_PANSCAN_FLIP` |
| 881 | `ID_PANSCAN_ROTATE_CCW` |
| 882 | `ID_PANSCAN_ROTATE_CW` |

### On-top modes

| ID | Symbol |
| --- | --- |
| 883 | `ID_ONTOP_NEVER` |
| 884 | `ID_ONTOP_ALWAYS` |
| 885 | `ID_ONTOP_WHILEPLAYING` |
| 886 | `ID_ONTOP_WHILEPLAYINGVIDEO` |

### Playback commands

| ID | Symbol |
| --- | --- |
| 887 | `ID_PLAY_PLAY` |
| 888 | `ID_PLAY_PAUSE` |
| 889 | `ID_PLAY_PLAYPAUSE` |
| 890 | `ID_PLAY_STOP` |
| 891 | `ID_PLAY_FRAMESTEP` |
| 892 | `ID_PLAY_FRAMESTEP_BACK` |
| 893 | `ID_PLAY_GOTO` |
| 894 | `ID_PLAY_DECRATE` |
| 895 | `ID_PLAY_INCRATE` |
| 896 | `ID_PLAY_RESETRATE` |
| 897 | `ID_PLAY_SEEKKEYBACKWARD` |
| 898 | `ID_PLAY_SEEKKEYFORWARD` |
| 899 | `ID_PLAY_SEEKBACKWARDSMALL` |
| 900 | `ID_PLAY_SEEKFORWARDSMALL` |
| 901 | `ID_PLAY_SEEKBACKWARDMED` |
| 902 | `ID_PLAY_SEEKFORWARDMED` |
| 903 | `ID_PLAY_SEEKBACKWARDLARGE` |
| 904 | `ID_PLAY_SEEKFORWARDLARGE` |
| 905 | `ID_PLAY_AUDIODELAY_PLUS` |
| 906 | `ID_PLAY_AUDIODELAY_MINUS` |
| 995 | `ID_PLAY_AUDIODELAY_ONOFF` |
| 1085 | `ID_PLAY_SEEKBEGIN` |
| 1201 | `ID_PLAY_REPEAT_AB` |
| 1202 | `ID_PLAY_REPEAT_AB_MARK_A` |
| 1203 | `ID_PLAY_REPEAT_AB_MARK_B` |

### Volume commands

| ID | Symbol |
| --- | --- |
| 907 | `ID_VOLUME_UP` |
| 908 | `ID_VOLUME_DOWN` |
| 909 | `ID_VOLUME_MUTE` |
| 910 | `ID_VOLUME_MUTE_OFF` |
| 911 | `ID_VOLUME_MUTE_DISABLED` |
| 970 | `ID_VOLUME_GAIN_INC` |
| 971 | `ID_VOLUME_GAIN_DEC` |
| 972 | `ID_VOLUME_GAIN_OFF` |
| 973 | `ID_VOLUME_GAIN_MAX` |

### After playback commands

| ID | Symbol |
| --- | --- |
| 912 | `ID_AFTERPLAYBACK_CLOSE` |
| 913 | `ID_AFTERPLAYBACK_STANDBY` |
| 914 | `ID_AFTERPLAYBACK_HIBERNATE` |
| 915 | `ID_AFTERPLAYBACK_SHUTDOWN` |
| 916 | `ID_AFTERPLAYBACK_LOGOFF` |
| 917 | `ID_AFTERPLAYBACK_LOCK` |
| 947 | `ID_AFTERPLAYBACK_NEXT` |
| 948 | `ID_AFTERPLAYBACK_DONOTHING` |
| 1029 | `ID_AFTERPLAYBACK_ONCE` |
| 1030 | `ID_AFTERPLAYBACK_EVERYTIME` |
| 1077 | `ID_AFTERPLAYBACK_EXIT` |
| 1078 | `ID_AFTERPLAYBACK_CLOSE_FILE` |
| 1079 | `ID_AFTERPLAYBACK_NEXT_LOOPED` |
| 1080 | `ID_AFTERPLAYBACK_CLOSE_FILE_AND_MINIMIZE` |
| 1081 | `ID_AFTERPLAYBACK_EVERYTIMEDONOTHING` |

### Navigation commands

| ID | Symbol |
| --- | --- |
| 919 | `ID_NAVIGATE_SKIPBACKFILE` |
| 920 | `ID_NAVIGATE_SKIPFORWARDFILE` |
| 921 | `ID_NAVIGATE_SKIPBACK` |
| 922 | `ID_NAVIGATE_SKIPFORWARD` |
| 923 | `ID_NAVIGATE_TITLEMENU` |
| 924 | `ID_NAVIGATE_ROOTMENU` |
| 925 | `ID_NAVIGATE_SUBPICTUREMENU` |
| 926 | `ID_NAVIGATE_AUDIOMENU` |
| 927 | `ID_NAVIGATE_ANGLEMENU` |
| 928 | `ID_NAVIGATE_CHAPTERMENU` |
| 929 | `ID_NAVIGATE_MENU_LEFT` |
| 930 | `ID_NAVIGATE_MENU_RIGHT` |
| 931 | `ID_NAVIGATE_MENU_UP` |
| 932 | `ID_NAVIGATE_MENU_DOWN` |
| 933 | `ID_NAVIGATE_MENU_ACTIVATE` |
| 934 | `ID_NAVIGATE_MENU_BACK` |
| 935 | `ID_NAVIGATE_MENU_LEAVE` |
| 974 | `ID_NAVIGATE_TUNERSCAN` |
| 1033 | `ID_NAVIGATE_SUBTITLES` |
| 1034 | `ID_NAVIGATE_AUDIO` |

### Menu, favorites, and help commands

| ID | Symbol |
| --- | --- |
| 936 | `ID_MENU_FAVORITES` |
| 937 | `ID_FAVORITES_ORGANIZE` |
| 938 | `ID_FAVORITES_ADD` |
| 939 | `ID_HELP_HOMEPAGE` |
| 940 | `ID_HELP_DONATE` |
| 941 | `ID_HELP_SHOWCOMMANDLINESWITCHES` |
| 942 | `ID_HELP_TOOLBARIMAGES` |
| 943 | `ID_HELP_ABOUT` |
| 944 | `ID_BOSS` |
| 949 | `ID_MENU_PLAYER_LONG` |
| 950 | `ID_MENU_PLAYER_SHORT` |
| 951 | `ID_MENU_FILTERS` |
| 975 | `ID_FAVORITES_QUICKADD` |
| 1000 | `ID_MENU_AUDIOLANG` |
| 1001 | `ID_MENU_SUBTITLELANG` |
| 1002 | `ID_MENU_JUMPTO` |
| 1003 | `ID_MENU_AFTERPLAYBACK` |
| 1006 | `ID_MENU_RECENT_FILES` |
| 1007 | `ID_START` |
| 1008 | `ID_SAVE` |
| 1018 | `ID_SHOW_HISTORY` |
| 1019 | `ID_RECENT_FILES_CLEAR` |
| 1032 | `ID_HELP_CHECKFORUPDATE` |

### Stream and subtitle/audio switching commands

| ID | Symbol |
| --- | --- |
| 952 | `ID_STREAM_AUDIO_NEXT` |
| 953 | `ID_STREAM_AUDIO_PREV` |
| 954 | `ID_STREAM_SUB_NEXT` |
| 955 | `ID_STREAM_SUB_PREV` |
| 956 | `ID_STREAM_SUB_ONOFF` |
| 961 | `ID_STREAM_VIDEO_NEXT` |
| 962 | `ID_STREAM_VIDEO_PREV` |
| 1150 | `ID_AUDIO_CENTER_INC` |
| 1151 | `ID_AUDIO_CENTER_DEC` |
| 1160 | `ID_AUDIO_OPTIONS` |
| 1170 | `ID_SUBTITLES_OPTIONS` |
| 1171 | `ID_SUBTITLES_ENABLE` |
| 1172 | `ID_SUBTITLES_STYLES` |
| 1173 | `ID_SUBTITLES_RELOAD` |
| 1175 | `ID_SUBTITLES_DEFSTYLE` |
| 1176 | `ID_SUBTITLES_FORCEDONLY` |
| 1177 | `ID_SUBTITLES_STEREO_DONTUSE` |
| 1178 | `ID_SUBTITLES_STEREO_SIDEBYSIDE` |
| 1179 | `ID_SUBTITLES_STEREO_TOPBOTTOM` |

### Misc playback and OSD commands

| ID | Symbol |
| --- | --- |
| 967 | `ID_REPEAT_FOREVER` |
| 984 | `ID_COLOR_BRIGHTNESS_INC` |
| 985 | `ID_COLOR_BRIGHTNESS_DEC` |
| 986 | `ID_COLOR_CONTRAST_INC` |
| 987 | `ID_COLOR_CONTRAST_DEC` |
| 988 | `ID_COLOR_HUE_INC` |
| 989 | `ID_COLOR_HUE_DEC` |
| 990 | `ID_COLOR_SATURATION_INC` |
| 991 | `ID_COLOR_SATURATION_DEC` |
| 992 | `ID_COLOR_RESET` |
| 994 | `ID_NORMALIZE` |
| 997 | `ID_COPY_IMAGE` |
| 1012 | `ID_SHIFT_SUB_DOWN` |
| 1013 | `ID_SHIFT_SUB_UP` |
| 1014 | `ID_GOTO_PREV_SUB` |
| 1015 | `ID_GOTO_NEXT_SUB` |
| 1021 | `ID_SHADERS_1_ENABLE` |
| 1022 | `ID_SHADERS_2_ENABLE` |
| 1036 | `ID_OSD_LOCAL_TIME` |
| 1037 | `ID_OSD_FILE_NAME` |
| 1039 | `ID_SHADERS_SELECT` |
| 1100 | `ID_SUB_POS_UP` |
| 1101 | `ID_SUB_POS_DOWN` |
| 1102 | `ID_SUB_POS_LEFT` |
| 1103 | `ID_SUB_POS_RIGHT` |
| 1104 | `ID_SUB_POS_RESTORE` |
| 1106 | `ID_SUB_COPYTOCLIPBOARD` |
| 1107 | `ID_SUB_SIZE_DEC` |
| 1108 | `ID_SUB_SIZE_INC` |
| 1110 | `ID_STEREO3D_AUTO` |
| 1111 | `ID_STEREO3D_MONO` |
| 1112 | `ID_STEREO3D_ROW_INTERLEAVED` |
| 1113 | `ID_STEREO3D_ROW_INTERLEAVED_2X` |
| 1114 | `ID_STEREO3D_HALFOVERUNDER` |
| 1115 | `ID_STEREO3D_OVERUNDER` |
| 1120 | `ID_STEREO3D_SWAP_LEFTRIGHT` |
| 1200 | `ID_SHOW_MILLISECONDS` |
| 1210 | `ID_ADDTOPLAYLISTROMCLIPBOARD` |
| 1211 | `ID_MOVEWINDOWBYVIDEO_ONOFF` |
| 1212 | `ID_PLAYLIST_OPENFOLDER` |

## Practical implications for the web API

Because `OnCommand()` forwards any positive `wm_command` to `WM_COMMAND`, the effective web API surface is:

- all positive command IDs from the `resource.h` command block
- plus the two special web-only commands:
  - `-1` seek
  - `-2` set volume

That means the following are all valid examples:

```text
/command.html?wm_command=887
/command.html?wm_command=889
/command.html?wm_command=899
/command.html?wm_command=902
/command.html?wm_command=907
/command.html?wm_command=909
/command.html?wm_command=921
/command.html?wm_command=952
/command.html?wm_command=1171
```

## Known good examples

### Play

```text
GET /command.html?wm_command=887
```

### Pause

```text
GET /command.html?wm_command=888
```

### Toggle play/pause

```text
GET /command.html?wm_command=889
```

### Stop

```text
GET /command.html?wm_command=890
```

### Exit player

```text
GET /command.html?wm_command=816
```

### Small backward seek

```text
GET /command.html?wm_command=899
```

### Small forward seek

```text
GET /command.html?wm_command=900
```

### Medium backward seek

```text
GET /command.html?wm_command=901
```

### Medium forward seek

```text
GET /command.html?wm_command=902
```

### Large backward seek

```text
GET /command.html?wm_command=903
```

### Large forward seek

```text
GET /command.html?wm_command=904
```

### Volume up / down / mute

```text
GET /command.html?wm_command=907
GET /command.html?wm_command=908
GET /command.html?wm_command=909
```

### Absolute seek to position

```text
GET /command.html?wm_command=-1&position=00:12:30
```

### Absolute seek by percent

```text
GET /command.html?wm_command=-1&percent=25
```

### Set absolute volume

```text
GET /command.html?wm_command=-2&volume=80
```

### Read live player state

```text
GET /variables.html
```

## MediaHive integration notes

Current MediaHive integration uses the MPC-BE web interface in two ways:

- frontend status indication through MediaHive's backend proxy
- native Python control in `mediahive.winmain`, which sends MPC-BE web requests directly

Current native command usage is centered on:

- `889` for play/pause
- `816` for exit
- `-1&position=HH:MM:SS` for exact 4-second seeking

## Guidance

- Prefer source-backed IDs from `resource.h` over icon inference from the HTML pages.
- Prefer `-1&position=...` or `-1&percent=...` when deterministic seek positioning is needed.
- Use `/variables.html` for timing and state.
- Treat the web interface as version-dependent: the command list here is accurate for the inspected upstream `master` branch and may differ across releases.

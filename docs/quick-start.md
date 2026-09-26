# Quick start

## Before connecting a device

Use macOS 13 or later, choose the release matching the Mac architecture, and work only with a device you own or are explicitly authorized to use. The [README installation section](https://github.com/hideouts-io/iOS-Developer-Toolkit#installation) is the canonical source for clone, source-launch, release-download, Gatekeeper, and removal instructions.

!!! warning "Keep one intended device connected"

    External providers such as UFADE, MVT, go-ios, idb, and ipsw use their own target-selection rules. A device selected in the toolkit does not constrain an external program.

## First session

1. Connect the unlocked device directly with a data-capable cable.
2. Approve the macOS accessory prompt and the iOS **Trust** prompt if shown.
3. Select the intended physical device in the top-right picker.
4. Open **Device & DDI** and check Developer Mode only if the intended workflow needs developer services.
5. Run **Capability Matrix** before mounting, tunneling, streaming, or changing state.
6. Choose a workspace and review its prerequisite, target, exact argument vector, and safety classification.
7. Stop streams, clear simulated location, finalize evidence, and unmount temporary developer support when finished.

The complete [first-device walkthrough](https://github.com/hideouts-io/iOS-Developer-Toolkit#first-device-walkthrough) explains each state and the expected failure indicators. For a controlled real-device validation, use the [physical-device test protocol](PHYSICAL_DEVICE_TEST_PROTOCOL.md).

## Learn without a physical device

Use **Demo Mode** for a visibly simulated interface walkthrough. It never exposes a fake device to operational code, and device actions remain disabled. The Action Palette (`⌘ K`) and keyboard reference (`⌘ /`) remain available for navigation.

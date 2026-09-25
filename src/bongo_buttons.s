    # DK Bongos buttons: spliced into codeA at its `srwi r12,r12,16` by
    # tools/inject_dol.py (repair_bongos), which carries the assembled words.
    # In: r12 = SI INBUFH. Free: r0. Out: r12, in GameCube button bits,
    # which codeA then maps onto the Wii Remote (A, B, Start, D-pad).
    # A bongo (both stick bytes zero) never sends its drums' A/B -- the
    # drums are only drums (codeB) -- and its clap (R) is A: attack. Start
    # stays Start (+, pause). Menus are driven with the Wii Remote, whose own
    # buttons codeA leaves untouched.
cave:
    andi.   0,12,0xFCFC         # stick bytes
    srwi    12,12,16            # (doesn't touch cr0)
    bne     back                # a real pad: unchanged
    andi.   12,12,0xFCFF        # drop A/B
    andi.   0,12,0x0020         # clap -> A
    beq     back
    ori     12,12,0x0100
back:
    .long 0

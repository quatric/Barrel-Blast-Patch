    # DK Bongos buttons: spliced into codeA at its `srwi r12,r12,16` by
    # tools/inject_dol.py (repair_bongos), which carries the assembled words.
    # In: r12 = SI INBUFH, r27 = channel. Free: r0, r10, r11. Out: r12, in
    # GameCube button bits, which codeA then maps onto the Wii Remote.
    # A bongo (both stick bytes zero) never sends its drums' A/B. A hit that
    # starts after ~0.5 s without drumming (a menu tap) becomes a D-pad
    # direction per sensor: left drum top Y = Up, bottom B = Down; right
    # drum top X = Right, bottom A = Left. The clap (R) is A, Start is also B.
    # State word per channel at 0x80001844: time base of the last drum
    # contact, low two bits 0 idle / 3 idle long enough / 1 menu tap /
    # 2 drumming. Time base: 60.75 MHz; >>20 = ~17.3 ms units.
    .set GAP, 29
cave:
    andi.   0,12,0xFCFC
    srwi    12,12,16
    bne     back
    lis     11,0x8000
    ori     11,11,0x1844
    slwi    10,27,2
    add     11,11,10
    lwz     0,0(11)
    andi.   10,12,0x0F00
    rlwinm  10,0,0,30,31
    beq     idle
    cmpwi   10,1
    blt     new_hit
    cmpwi   10,3
    beq     armed
    b       refresh
new_hit:
    mftb    10
    subf    10,0,10
    srwi    10,10,20
    cmplwi  10,GAP
    li      10,1
    bgt     refresh
    li      10,2
    b       refresh
armed:
    li      10,1
refresh:
    mftb    0
    rlwinm  0,0,0,0,29
    or      0,0,10
    stw     0,0(11)
    cmpwi   10,1
    bne     strip
    li      11,0
    andi.   10,12,0x0800        # Y: left drum top    -> Up
    beq     1f
    ori     11,11,0x0008
1:  andi.   10,12,0x0200        # B: left drum bottom -> Down
    beq     1f
    ori     11,11,0x0004
1:  andi.   10,12,0x0400        # X: right drum top   -> Right
    beq     1f
    ori     11,11,0x0002
1:  andi.   10,12,0x0100        # A: right drum bottom -> Left
    beq     1f
    ori     11,11,0x0001
1:  or      12,12,11
    b       strip
idle:
    cmpwi   10,3
    beq     strip
    rlwinm  0,0,0,0,29
    mftb    10
    subf    10,0,10
    srwi    10,10,20
    cmplwi  10,GAP
    ble     idle_store
    ori     0,0,3
idle_store:
    stw     0,0(11)
strip:
    andi.   12,12,0xFCFF        # the drums never send A/B
    andi.   0,12,0x0020         # clap -> A
    beq     1f
    ori     12,12,0x0100
1:  andi.   0,12,0x1000         # Start -> also B (menu back)
    beq     back
    ori     12,12,0x0200
back:
    .long 0

/* Present a Classic Controller to the game as a Wii Remote + Nunchuk.
 *
 * Barrel Blast only understands the Nunchuk: its left drum is a Nunchuk
 * shake, and it reads the Nunchuk stick for movement. read_kpad_acc only
 * runs its Nunchuk motion block for Nunchuk-type samples, so a Classic
 * Controller's left drum never reached the game -- and forcing that block
 * on for Classic samples broke it outright. Instead this runs on the game's
 * own copy of each KPADStatus, right after its KPADRead call returns
 * (0x8003A788), once KPAD and every hook have finished. For a Classic
 * Controller entry it:
 *
 *   - copies the right stick into the Nunchuk stick fields (movement; the
 *     left stick drives the pointer),
 *   - writes codeD's stick-driven pointer position, so the Wii Remote's own
 *     IR can't fight it on samples KPAD didn't run the IR routine for,
 *   - turns a left-drum press into the same 6-sample stroke codeB writes
 *     for a GameCube pad (+-10.0 on Nunchuk acc Y, alternating each sample,
 *     10.0 into the Nunchuk acc_value/acc_speed fields),
 *   - reports dev_type 1 (Nunchuk).
 *
 * Left drum = X / L, ZR (the both-drums jump, as the GameCube pad's Z), or
 * the analog L trigger past ~40/255. ZL does nothing. The right drum already works
 * through the Wii Remote motion path (codeB), and the pointer, button
 * mapping and HOME Menu handling ran earlier on KPAD's own status.
 *
 * No floating-point arithmetic (compiled -msoft-float): floats are only
 * copied or compared as bit patterns (valid for non-negative values). No
 * globals; per-channel state lives in the injected section's scratch.
 */
typedef unsigned int u32;
typedef unsigned char u8;

#define W(p, off)   (*(volatile u32 *)((u8 *)(p) + (off)))
#define B(p, off)   (*(volatile u8 *)((u8 *)(p) + (off)))

#define KPAD_SIZE   0x84
#define STATE       ((volatile u8 *)0x80001860)   /* scratch +0x40: 4 x {prev, count, -, -} */

#define CL_HOLD     0x60
#define CL_RSTICK_X 0x74
#define CL_RSTICK_Y 0x78
#define POS_X       0x20
#define POS_Y       0x24
#define VEC_X       0x28
#define VEC_Y       0x2C
#define DPD_VALID   0x5E
#define CODED_POS   (*(volatile u32 *)0x80001870)   /* scratch +0x50 */
#define CL_TRIG_L   0x7C
#define NC_STICK_X  0x60
#define NC_STICK_Y  0x64
#define NC_ACC_X    0x68
#define NC_ACC_Y    0x6C
#define NC_ACC_Z    0x70
#define NC_ACC_VAL  0x74
#define NC_ACC_SPD  0x78
#define DEV_TYPE    0x5C

#define LEFT_BUTTONS  (0x2000 | 0x0008 | 0x0004)            /* L, X, ZR */
#define TRIG_ON       0x3E20A0A1        /* 40/255 = 0.157f */
#define F_10          0x41200000        /* 10.0f */
#define F_M10         0xC1200000        /* -10.0f */

void cc_convert(u32 count, u8 *entry, u32 chan)
{
    volatile u8 *st;
    u32 i;

    if (chan > 3 || count > 16)
        return;
    st = STATE + chan * 4;

    for (i = 0; i < count; i++, entry += KPAD_SIZE) {
        u32 buttons, lx, ly, press;

        if (B(entry, DEV_TYPE) != 2)
            continue;

        buttons = W(entry, CL_HOLD);
        lx = W(entry, CL_RSTICK_X);
        ly = W(entry, CL_RSTICK_Y);
        press = (buttons & LEFT_BUTTONS) ||
                (!(W(entry, CL_TRIG_L) & 0x80000000) && W(entry, CL_TRIG_L) > TRIG_ON);

        if (press && !st[0])
            st[1] = 6;                  /* new press: start a stroke */
        st[0] = press ? 1 : 0;

        W(entry, NC_STICK_X) = lx;
        W(entry, NC_STICK_Y) = ly;
        W(entry, NC_ACC_X) = 0;
        W(entry, NC_ACC_Z) = 0;
        if (st[1]) {
            u32 tb;
            st[1]--;
            /* Flip once per ~17 ms (time-base bit 20), not per sample: the
             * SDK averages a frame's samples, so alternating per sample
             * cancels out whenever a frame has an even number of them. */
            __asm__ volatile ("mftb %0" : "=r"(tb));
            W(entry, NC_ACC_Y) = (tb & 0x00100000) ? F_M10 : F_10;
            W(entry, NC_ACC_VAL) = F_10;
            W(entry, NC_ACC_SPD) = F_10;
        } else {
            W(entry, NC_ACC_Y) = 0;
            W(entry, NC_ACC_VAL) = 0;
            W(entry, NC_ACC_SPD) = 0;
        }
        /* Not while the HOME Menu is open: it uses the real Wii Remote's
         * pointer (CHomeButtonMenu singleton, vtable-checked, open flag). */
        if (CODED_POS && !(W(0x80531B80, 0) == 0x802E7288 && B(0x80531BB2, 0))) {
            volatile u32 *pos = (volatile u32 *)CODED_POS + chan * 2;
            W(entry, POS_X) = pos[0];
            W(entry, POS_Y) = pos[1];
            W(entry, VEC_X) = pos[0];
            W(entry, VEC_Y) = pos[1];
            B(entry, DPD_VALID) |= 2;
        }
        B(entry, DEV_TYPE) = 1;
    }
}

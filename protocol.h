/* wave-shim wire protocol — stdio, length-prefixed, binary.
 *
 * Request   : [u32 len][u8 op ][payload ...]      len counts op+payload
 * Response  : [u32 len][u8 stat][payload ...]      stat 0 = OK, else error
 *
 * All integers little-endian. No text framing, no delimiters, no escaping:
 * the length prefix is the only structure. An error response's payload is a
 * UTF-8 message with no trailing NUL.
 */
#ifndef WAVE_SHIM_PROTOCOL_H
#define WAVE_SHIM_PROTOCOL_H

#define OP_HELLO    0x01  /* ()                          -> text banner + caps */
#define OP_OPEN     0x02  /* (u8 kind, u8 rate)          -> u32 handle         */
#define OP_CLOSE    0x03  /* (u32 handle)                -> ()                 */
#define OP_RESET    0x04  /* (u32 handle)                -> ()                 */
#define OP_PROCESS  0x05  /* (u32 handle, bytes in)      -> bytes out          */

#define ST_OK       0x00
#define ST_ERR      0x01  /* payload = human-readable reason                   */

#define KIND_ENCODER 0
#define KIND_DECODER 1

#define RATE_TDMA_AMBE2 0  /* XisTdmaAmbe — AMBE+2, 49 bits / 20 ms */
#define RATE_FDMA_IMBE  1  /* XisFdmaAmbe — IMBE,   88 bits / 20 ms */

#define MAX_FRAME (1u << 20)

#endif

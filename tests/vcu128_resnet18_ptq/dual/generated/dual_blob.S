.section .rodata.dual_blob,"a",@progbits
.balign 64
.globl dual_blob
dual_blob:
.incbin "generated/dual_blob.bin"
.globl dual_blob_end
dual_blob_end:
.section .note.GNU-stack,"",@progbits

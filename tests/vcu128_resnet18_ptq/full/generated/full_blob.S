.section .rodata.full_blob,"a",@progbits
.balign 64
.globl full_blob
full_blob:
.incbin "generated/full_blob.bin"
.globl full_blob_end
full_blob_end:
.section .note.GNU-stack,"",@progbits

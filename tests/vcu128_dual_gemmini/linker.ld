OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { APP (rwx) : ORIGIN = 0x80000000, LENGTH = 64K }
PHDRS { code PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
 . = ORIGIN(APP);
 .text : { KEEP(*(.text.init)) *(.text .text.*) } > APP :code
 .rodata : { *(.rodata .rodata.*) *(.srodata .srodata.*) } > APP :code
 . = ALIGN(64);
 .data : { *(.data .data.*) *(.sdata .sdata.*) } > APP :data
 PROVIDE(__global_pointer$ = ADDR(.data) + 0x800);
 .bss (NOLOAD) : {
  __bss_start = .; *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON)
  . = ALIGN(16); __bss_end = .;
 } > APP :data
 __image_end = .;
 _stack_top = ORIGIN(APP) + LENGTH(APP);
 _stack_bottom = _stack_top - 0x8000;
 ASSERT(__image_end <= _stack_bottom, "image overlaps two 16 KiB stacks")
 /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}

OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { APP (rwx) : ORIGIN = 0x80000000, LENGTH = 240M }
PHDRS { code PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
 . = ORIGIN(APP);
 .text : {
  KEEP(*(.text.init))
  *(.text .text.*)
 } > APP :code
 .rodata : {
  KEEP(*(.rodata.build_info))
  *(.rodata .rodata.*) *(.srodata .srodata.*)
 } > APP :code
 . = ALIGN(64);
 .data : { *(.data .data.*) *(.sdata .sdata.*) } > APP :data
 PROVIDE(__global_pointer$ = ADDR(.data) + 0x800);
 .bss (NOLOAD) : {
  . = ALIGN(64); __bss_start = .;
  *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON)
  . = ALIGN(64); __bss_end = .;
 } > APP :data
 __image_end = .;
 _stack_bottom = 0x8fe00000;
 _stack_top = 0x8ff00000;
 __result_base = 0x8ff00000;
 __log_base = 0x8ff10000;
 ASSERT(__image_end <= 0x8f000000, "full image exceeds 240 MiB APP window")
 ASSERT((__bss_start % 8) == 0 && (__bss_end % 8) == 0, "BSS alignment")
 /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}

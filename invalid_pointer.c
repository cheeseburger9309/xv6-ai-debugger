#include "types.h"
#include "stat.h"
#include "user.h"

int main(int argc, char *argv[]) {
  printf(1, "invalid_pointer: Testing access to invalid memory address...\n");
  
  // Try to access memory at an invalid (unmapped) address
  int *ptr = (int *)0xDEADBEEF;  // Invalid address
  int value = *ptr;  // This will cause page fault
  
  printf(1, "Value: %d (should not print)\n", value);
  exit();
}


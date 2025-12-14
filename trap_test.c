#include "types.h"
#include "stat.h"
#include "user.h"

int main(void) {
  printf(1, "trap_test: Triggering page fault...\n");
  
  // Call the system call that triggers the panic
  triggerpanic();
  
  printf(1, "trap_test: Should not reach here!\n");
  exit();
}


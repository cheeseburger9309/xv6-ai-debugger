#include "types.h"
#include "stat.h"
#include "user.h"

int main(int argc, char *argv[]) {
  printf(1, "null_deref: Testing NULL pointer dereference...\n");
  
  int *ptr = 0;  // NULL pointer
  int value = *ptr;  // Dereference NULL
  
  printf(1, "Value: %d (should not print)\n", value);
  exit();
}


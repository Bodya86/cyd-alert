// Мінімальна заглушка Arduino для тестів на ПК
#pragma once
#include <string>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
class String : public std::string { public: using std::string::string; String() {} String(const std::string& s) : std::string(s) {} };

cmake_minimum_required(VERSION 3.22.1)
project(tsf_jni)

add_library(tsf_jni SHARED tsf_jni.cpp)

find_library(log-lib log)

target_link_libraries(tsf_jni ${log-lib})

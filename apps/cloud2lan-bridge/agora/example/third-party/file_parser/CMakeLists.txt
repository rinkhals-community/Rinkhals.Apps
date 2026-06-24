cmake_minimum_required(VERSION 2.4)
project(file_parser)

include_directories(${PROJECT_SOURCE_DIR}/include)
link_directories(${PROJECT_SOURCE_DIR}/lib)

aux_source_directory(./src SRCS)

set(LIBRARY_OUTPUT_PATH ${PROJECT_SOURCE_DIR}/lib/${MACHINE})

add_library(${PROJECT_NAME} STATIC ${SRCS})

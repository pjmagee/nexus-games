cmake_minimum_required(VERSION 3.20)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
    message(FATAL_ERROR "In-source build disallowed. Use: cmake -S src/game-capture -B build/cpp/capture -G 'Visual Studio 17 2022' -A x64")
endif()

project(hots_capture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(hots_capture src/main.cpp)

target_compile_definitions(hots_capture PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS)
target_link_libraries(hots_capture PRIVATE d3d11 dxgi WindowsApp)

if (MSVC)
    target_compile_options(hots_capture PRIVATE /W4 /permissive- /EHsc)
endif()

set(OUTPUT_BIN_DIR "${CMAKE_BINARY_DIR}/bin")
file(MAKE_DIRECTORY ${OUTPUT_BIN_DIR})
set_target_properties(hots_capture PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${OUTPUT_BIN_DIR}
    RUNTIME_OUTPUT_DIRECTORY_DEBUG ${OUTPUT_BIN_DIR}/Debug
    RUNTIME_OUTPUT_DIRECTORY_RELEASE ${OUTPUT_BIN_DIR}/Release
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO ${OUTPUT_BIN_DIR}/RelWithDebInfo
    RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL ${OUTPUT_BIN_DIR}/MinSizeRel
)

find_program(CLANG_FORMAT_EXE NAMES clang-format clang-format.exe)
if (CLANG_FORMAT_EXE)
    message(STATUS "clang-format found: ${CLANG_FORMAT_EXE}")
    set(HOOK_SOURCE_FILES ${CMAKE_CURRENT_SOURCE_DIR}/src/main.cpp)
    add_custom_target(format
        COMMAND ${CLANG_FORMAT_EXE} -i ${HOOK_SOURCE_FILES}
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        COMMENT "Running clang-format on sources" VERBATIM)
    add_custom_target(format-check
        COMMAND ${CLANG_FORMAT_EXE} --dry-run --Werror ${HOOK_SOURCE_FILES}
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        COMMENT "Checking clang-format style (no changes)" VERBATIM)
else()
    message(STATUS "clang-format not found; 'format' targets will be unavailable")
endif()
